import asyncio
import gzip
import json
import math
import os
import statistics
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path

from analysis import fit_usl, histogram_quantile
from load import load, summarize

BASE = 'http://localhost:8080'
PROM = 'http://localhost:9090'
OUT = Path('results')
SEL = 'job="web",method="GET",path="/vaccinations/{id}"'
DURATION = 'http_request_duration_seconds_bucket'
CPU = 'container_cpu_usage_seconds_total{job="cadvisor",namespace="default",container="web"}'
READY = 'kube_deployment_status_replicas_ready{namespace="default",deployment="web"}'
DESIRED = 'kube_horizontalpodautoscaler_status_desired_replicas{namespace="default",horizontalpodautoscaler="web"}'


def k(*args):
    return subprocess.check_output(['kubectl','--context','kind-pr3',*args],text=True)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def api(method, path, data=None):
    req=urllib.request.Request(BASE+path, data=json.dumps(data).encode() if data else None,
                               headers={'Content-Type':'application/json'}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        body=r.read()
        return dict(status=r.status,pod=r.headers.get('X-Pod'),body=json.loads(body) if body else None)


def record(i):
    return dict(patient_code=f'PR3-{i}',vaccine='Test',batch='PR3',dose_number=1,
                administration_date='2024-01-01',facility='Test')


def prom(expr, at=None, start=None):
    params={'query':expr}
    path='query'
    if start is not None:
        path='query_range'
        params.update(start=start,end=at,step=5)
    elif at is not None:
        params['time']=at
    with urllib.request.urlopen(PROM+'/api/v1/'+path+'?'+urllib.parse.urlencode(params),timeout=15) as r:
        data=json.load(r)
    if data['status']!='success':
        raise RuntimeError(data)
    return data['data']['result']


def vector(expr, at, label):
    return {s['metric'].get(label,'all'):float(s['value'][1]) for s in prom(expr,at)
            if math.isfinite(float(s['value'][1]))}


def scale(n):
    k('scale','deployment/web',f'--replicas={n}')
    k('rollout','status','deployment/web','--timeout=180s')
    wait(lambda: ready_count()==n,180)


def ready_count():
    return json.loads(k('get','deployment/web','-o','json'))['status'].get('readyReplicas',0)


def wait(check, timeout, hold=0):
    deadline=time.monotonic()+timeout
    since=None
    while time.monotonic()<deadline:
        if check():
            since=since or time.monotonic()
            if time.monotonic()-since>=hold:
                return
        else:
            since=None
        time.sleep(2)
    raise RuntimeError('Condition did not become stable before timeout')


def validate():
    test=record('check')
    created=api('POST','/vaccinations',test)
    ident=created['body']['id']
    path=f'/vaccinations/{ident}'
    read=api('GET',path)
    updated=api('PUT',path,{**test,'dose_number':2})
    assert read['body']['id']==ident and updated['body']['dose_number']==2
    k('delete','pod/db-0','--wait=true','--timeout=180s')
    k('wait','--for=condition=Ready','pod/db-0','--timeout=180s')
    k('rollout','status','deployment/web','--timeout=180s')
    def restored():
        try: return api('GET',path)
        except OSError: return None
    wait(restored,120)
    after=restored()
    assert after['body']['dose_number']==2
    deleted=api('DELETE',path)
    save('checks.json',dict(crud=[created,read,updated,deleted],after_db_restart=after,
                            top=k('top','pods'),nodes=k('get','nodes','-o','wide')))
    expressions=(READY,'pg_up == 1','up{job="web"} == 1',
                 'container_cpu_cfs_periods_total{container="web"}',
                 'http_requests_total{'+SEL+'}')
    evidence={}
    for expr in expressions:
        wait(lambda: bool(prom(expr)),120)
        evidence[expr]=prom(expr)
    save('monitoring-checks.json',evidence)


def buckets(at, pod=None):
    selector=SEL+(f',pod="{pod}"' if pod else '')
    rows=prom(f'sum by(le) (increase({DURATION}{{{selector}}}[20s]))',at)
    return histogram_quantile([(float(r['metric']['le']),float(r['value'][1])) for r in rows])


def throttle(at):
    start = at-20
    selector = '{job="cadvisor",namespace="default",container="web",__name__=~"container_cpu_cfs_(periods|throttled_periods)_total"}'
    series = prom(selector+'[70s]',at+25)
    grouped = {}
    for item in series:
        labels = item['metric']
        key = tuple(sorted((k,v) for k,v in labels.items() if k!='__name__'))
        name = labels['__name__']
        grouped.setdefault(key,{})[name] = {float(t):float(v) for t,v in item['values']}
    evidence = {}
    for key, metrics in grouped.items():
        pod = dict(key).get('pod')
        periods = metrics.get('container_cpu_cfs_periods_total',{})
        throttled = metrics.get('container_cpu_cfs_throttled_periods_total',{})
        common = sorted(t for t in periods.keys() & throttled.keys()
                        if math.isfinite(periods[t]) and math.isfinite(throttled[t]))
        left = max((t for t in common if start-25<=t<=start),default=None)
        right = min((t for t in common if at<=t<=at+25),default=None)
        if not pod or left is None or right is None:
            continue
        window = [t for t in common if left<=t<=right]
        if any(periods[b]<periods[a] or throttled[b]<throttled[a] for a,b in zip(window,window[1:])):
            continue
        dp, dt = periods[right]-periods[left], throttled[right]-throttled[left]
        if dp>0 and 0<=dt<=dp:
            row = dict(start=left,end=right,periods=dp,throttled=dt,ratio=dt/dp)
            if pod not in evidence or right-left < evidence[pod]['end']-evidence[pod]['start']:
                evidence[pod]=row
    return {pod:r['ratio'] for pod,r in evidence.items()}, evidence


def raw(name,data):
    with gzip.open(OUT/(name+'.json.gz'),'wt',encoding='utf-8') as f:
        json.dump(data,f,separators=(',',':'),ensure_ascii=False)


def run(url, segments, name):
    print(name,segments,flush=True)
    data=asyncio.run(load(url,segments))
    raw(name,data)
    reject_clock_gap(data,name)
    return data


def reject_clock_gap(data,name):
    if data.get('clock_gaps'):
        raise RuntimeError(f'{name}: execution paused for more than 3 s; raw data saved, repeat without sleep.')


def staircase(url,n,max_rate):
    scale(n)
    time.sleep(10)
    run(url,[(60,100)],f'warm-{n}')
    consecutive=0
    steps=[]
    def stop(rows, end, rate):
        nonlocal consecutive
        if end == 10:
            return False
        stat=summarize(rows,end-20,end)
        consecutive=consecutive+1 if stat['achieved']<.9*rate else 0
        print('N',n,'target',rate,'sent',stat['sent'],'achieved',stat['achieved'],flush=True)
        return consecutive>=2
    stop.linux_policy = 'capacity90'
    stop.replicas = n
    segments=[(10,200)]+[(30,rate) for rate in range(200,max_rate+1,200)]
    data=asyncio.run(load(url,segments,stop))
    raw(f'fixed-{n}',data)
    reject_clock_gap(data,f'fixed-{n}')
    time.sleep(30)
    end=10
    for duration,rate in segments[1:]:
        end+=duration
        if end>data['duration']:
            break
        stat=summarize(data['rows'],end-20,end)
        at=data['epoch']+end
        throttles, cfs_evidence=throttle(at)
        counts=stat['pods']
        stat.update(target=rate,server=buckets(at),throttle=throttles,
                    throttle_mean=statistics.mean(throttles.values()) if throttles else None,
                    throttle_max=max(throttles.values(),default=None))
        current=json.loads(k('get','pods','-l','app=web','-o','json'))['items']
        names=[item['metadata']['name'] for item in current if not item['metadata'].get('deletionTimestamp')]
        throttles={pod:throttles[pod] for pod in names if pod in throttles}
        stat.update(throttle=throttles,cfs_evidence={pod:cfs_evidence[pod] for pod in throttles},
                    throttle_mean=statistics.mean(throttles.values()) if len(throttles)==n else None,
                    throttle_max=max(throttles.values()) if len(throttles)==n else None)
        stat['pod_rps']={pod:counts.get(pod,0)/20 for pod in names}
        stat['imbalance']=max(counts.values(),default=0)*n/sum(counts.values()) if counts else None
        if names:
            extreme=sorted(names,key=lambda name:counts.get(name,0))
            stat['extremes']=[dict(pod=pod,p99=buckets(at,pod),throttle=throttles.get(pod)) for pod in (extreme[0],extreme[-1])]
        stat['pod_series']=prom(f'sum by(pod)(rate(http_requests_total{{{SEL}}}[15s]))',at,start=at-30)
        steps.append(stat)
    best=max(steps,key=lambda s:s['achieved'])
    return dict(replicas=n,x=best['achieved'],std=best['std'],best=best,steps=steps,
                generator_limited=steps[-1]['achieved']>=.9*steps[-1]['target'])


def align(series, epoch, seconds):
    samples=sorted((float(t),float(v)) for s in series for t,v in s['values'] if math.isfinite(float(v)))
    result=[]
    index=0
    previous=None
    for t in range(seconds):
        while index<len(samples) and samples[index][0]<=epoch+t:
            previous=samples[index][1]
            index+=1
        result.append(previous)
    return result


def profile(url, segments, name, capacities, threshold=None, fixed_p99=None):
    data=run(url,segments,name)
    duration=sum(s[0] for s in segments)
    e=data['epoch']
    time.sleep(7)
    expressions={'ready':READY,'desired':DESIRED,'cpu':f'avg(rate({CPU}[15s])) / 0.5 * 100'}
    series={key:prom(expr,e+duration,start=e-5) for key,expr in expressions.items()}
    save(name+'-prom.json',series)
    ready=align(series['ready'],e,duration)
    desired=align(series['desired'],e,duration)
    if None in ready: raise RuntimeError('Missing ready replica samples')
    target=[rate for seconds,rate in segments for _ in range(seconds)]
    demand=[next((i+1 for i,x in enumerate(capacities) if x>=r),4) for r in target]
    bins=[[] for _ in range(duration)]
    for row in data['rows']:
        for t in {int(row[i]) for i in (0,1,2) if row[i] is not None}:
            if 0<=t<duration:
                bins[t].append(row)
    seconds=[summarize(bins[t],t,t+1) for t in range(duration)]
    first=summarize(data['rows'],0,30)['p99']
    jump=summarize(data['rows'],30,90)['p99']
    if first is None or jump is None:
        raise RuntimeError('No completed requests in the baseline or jump window')
    threshold=threshold if threshold is not None else 2*first
    delta=[s-d for s,d in zip(ready,demand)]
    p99=[s['p99'] for s in seconds]
    peak=max(ready)
    timings={}
    if fixed_p99 is not None:
        if None in desired: raise RuntimeError('Missing desired replica samples')
        timings=dict(peak_ready_any=next((t-30 for t in range(30,duration) if peak>ready[29] and ready[t]==peak),None),
            desired_change=next((t-30 for t in range(30,90) if desired[t]!=desired[29]),None),
            peak_ready=next((t-30 for t in range(30,90) if peak>ready[29] and ready[t]==peak),None),
            p99_recovery=next((t-30 for t in range(30,81) if all(v is not None and v<1.2*fixed_p99 for v in p99[t:t+10])),None),
            scale_down=next((t-90 for t in range(90,duration) if ready[t]<ready[t-1]),None))
    result=dict(T=duration,threshold=threshold,jump_p99=jump,peak_ready=peak,
        theta_u=sum(v<0 for v in delta)/duration,theta_o=sum(v>0 for v in delta)/duration,
        a_u=sum(max(-v,0) for v in delta)/duration,a_o=sum(max(v,0) for v in delta)/duration,
        W=sum(ready),violations=sum(v is not None and v>threshold for v in p99),
        non2xx=sum(s['non2xx'] for s in seconds),connection=sum(s['connection'] for s in seconds),
        timings=timings,target=target,ready=ready,desired=desired,demand=demand,p99=p99,
        achieved=[s['achieved'] for s in seconds],cpu=align(series['cpu'],e,duration))
    save(name+'-summary.json',result)
    return result


def enable_hpa(window):
    spec=Path('k8s/hpa.yaml').read_text().replace('stabilizationWindowSeconds: 30',f'stabilizationWindowSeconds: {window}')
    subprocess.run(['kubectl','--context','kind-pr3','apply','-f','-'],input=spec,text=True,check=True)
    def stable():
        h=json.loads(k('get','hpa/web','-o','json')).get('status',{})
        return (h.get('currentReplicas')==h.get('desiredReplicas')==ready_count()==1
                and bool(h.get('currentMetrics')))
    wait(stable,600,hold=60)


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--max-rate',type=int,default=2400)
    stage = p.add_mutually_exclusive_group()
    stage.add_argument('--capacity-only', action='store_true')
    stage.add_argument('--profiles-only', action='store_true')
    args=p.parse_args()
    if args.max_rate < 200:
        p.error('--max-rate must be at least 200')
    OUT.mkdir(exist_ok=True)
    if not args.profiles_only and (OUT/'fixed-1.json.gz').exists():
        raise SystemExit('Move the previous results folder before a new run.')
    if args.profiles_only:
        if any((OUT/(name+'.json.gz')).exists() for name in ('static','hpa-30','d5-30','d5-300')):
            raise SystemExit('Profile data already exists; preserve it before repeating profiles.')
        status=json.loads((OUT/'status.json').read_text(encoding='utf-8'))
        if status.get('state') != 'capacity_complete':
            raise SystemExit('A completed capacity stage is required first.')
        if status.get('generator_backend') != os.environ.get('PR3_LOAD_BACKEND','native'):
            raise SystemExit('Capacity and profile generator backends must match.')
        ids=json.loads((OUT/'seed.json').read_text(encoding='utf-8'))
        points=json.loads((OUT/'capacity.json').read_text(encoding='utf-8'))
    else:
        k('delete','hpa/web','--ignore-not-found')
        scale(1)
        validate()
        ids=[api('POST','/vaccinations',record(i))['body']['id'] for i in range(100)]
        save('seed.json',ids)
        machine=dict(docker=subprocess.check_output(['docker','info','--format','{{.NCPU}} CPU, {{.MemTotal}} bytes'],text=True).strip(), nodes=k('get','nodes','-o','wide'),generator_backend=os.environ.get('PR3_LOAD_BACKEND','native'))
        if os.environ.get('PR3_LOAD_BACKEND') == 'linux':
            machine['generator_image']=subprocess.check_output(['docker','image','inspect','pr3-generator-probe:local','--format','{{.Id}}'],text=True).strip()
        save('machine.json',machine)
        points=[]
        for n in range(1,5):
            points.append(staircase(f'{BASE}/vaccinations/{ids[0]}',n,args.max_rate))
            save('capacity.json',points)
    url=f'{BASE}/vaccinations/{ids[0]}'
    fit=fit_usl(points)
    save('usl.json',fit)
    if args.capacity_only:
        save('status.json',dict(state='capacity_complete',fit_valid=fit['valid'],
                               generator_backend=os.environ.get('PR3_LOAD_BACKEND','native')))
        print('Capacity stage finished. Review capacity.json, usl.json and raw data before profiles.',flush=True)
        return
    if not fit['valid']:
        raise RuntimeError(fit['reason'])
    if any(p['generator_limited'] for p in points):
        raise RuntimeError('A fixed run ended above 90% of its last target. Move results/ aside and raise --max-rate.')
    caps=[p['x'] for p in points]
    if min(caps)<=0:
        raise RuntimeError('A capacity is zero; check raw results before proceeding.')
    low=.3*caps[0]
    high=min(2*caps[0],.9*caps[3])
    save('profile-settings.json',dict(low=low,high=high))
    scale(4)
    time.sleep(10)
    base=profile(url,[(30,low),(60,high),(90,low)],'static',caps)
    for name, window, tail in [('hpa-30',30,90),('d5-30',30,360),('d5-300',300,360)]:
        k('delete','hpa/web','--ignore-not-found')
        scale(1)
        enable_hpa(window)
        profile(url,[(30,low),(60,high),(tail,low)],name,caps,base['threshold'],base['jump_p99'])
    from report import main as report
    report()
    save('status.json',dict(state='complete'))


if __name__=='__main__':
    main()
