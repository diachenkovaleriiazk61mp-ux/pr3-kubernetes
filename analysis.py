import math
import statistics
from collections import Counter


def histogram_quantile(buckets, q=.99):

    buckets=sorted(buckets)
    if not buckets or buckets[-1][1]<=0:
        return dict(value=None, lower=None, upper=None, error_bound=None, overflow=False)
    rank=q*buckets[-1][1]
    lower, previous=0.0,0.0
    for upper,count in buckets:
        if count>=rank:
            if math.isinf(upper):
                return dict(value=lower,lower=lower,upper=None,error_bound=None,overflow=True)
            value=lower+(upper-lower)*(rank-previous)/(count-previous) if count>previous else upper
            return dict(value=value,lower=lower,upper=upper,error_bound=upper-lower,overflow=False)
        lower,previous=upper,count
    raise ValueError('Malformed cumulative histogram')


def fit_usl(points):
    by_n={p['replicas']:p for p in points}
    if 1 not in by_n or by_n[1]['generator_limited']:
        return dict(valid=False,reason='N=1 is generator-limited or absent')
    base=by_n[1]['x']
    selected=[p for p in points if p['replicas']>1 and not p['generator_limited']]
    if base<=0 or len(selected)<2:
        return dict(valid=False,reason='Fewer than two valid points with N>1')
    transformed=[]
    for p in selected:
        n=p['replicas'];c=p['x']/base
        if c<=0:return dict(valid=False,reason='Nonpositive measured throughput')
        transformed.append(dict(n=n,c=c,u=n-1,v=n*(n-1),y=n/c-1))
    uu=sum(t['u']**2 for t in transformed);vv=sum(t['v']**2 for t in transformed)
    uv=sum(t['u']*t['v'] for t in transformed);uy=sum(t['u']*t['y'] for t in transformed)
    vy=sum(t['v']*t['y'] for t in transformed);den=uu*vv-uv**2
    raw_s=(vv*uy-uv*vy)/den;raw_k=(uu*vy-uv*uy)/den

    candidates=[(max(0,uy/uu),0), (0,max(0,vy/vv)), (0,0)]
    if raw_s>=0 and raw_k>=0:candidates.append((raw_s,raw_k))
    sigma,kappa=min(candidates,key=lambda sk:sum((t['y']-sk[0]*t['u']-sk[1]*t['v'])**2 for t in transformed))
    peak=math.sqrt((1-sigma)/kappa) if kappa>0 and sigma<1 else None
    return dict(valid=True,lambda_=base,sigma=sigma,kappa=kappa,n_star=peak,
                asymptote=base/sigma if kappa==0 and sigma>0 else None,
                raw_sigma=raw_s,raw_kappa=raw_k,points=transformed,
                sums=dict(u2=uu,v2=vv,uv=uv,uy=uy,vy=vy,denominator=den))


def select_capacity(run,g,replicas):
    if not run['steps']:
        raise ValueError('No completed steps')
    best=max(run['steps'],key=lambda s:s['achieved_rps'])
    last=run['steps'][-1]
    limited=best['achieved_rps']>=.9*g or last['achieved_rps']>=.9*last['target_rps']
    return dict(replicas=replicas,x=best['achieved_rps'],std=best['std_rps'],
                step_rate=best['target_rps'],generator_limited=limited,best=best)


def matrix_series(data):
    return {tuple(sorted(s['metric'].items())):s['values'] for s in data}


def prometheus_range_stats(matrix):
    values=[float(v) for s in matrix for _,v in s['values'] if math.isfinite(float(v))]
    return statistics.mean(values) if values else None
