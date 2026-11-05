from collections import Counter
import gzip
import json
import math
import statistics
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

OUT = Path('results')
FIG = Path('figures')
NAMES = ('static', 'hpa-30', 'd5-30', 'd5-300')


def read(name):
    return json.loads((OUT / (name + '.json')).read_text(encoding='utf-8-sig'))


def raw(name):
    with gzip.open(OUT / (name + '.json.gz'), 'rt', encoding='utf-8') as stream:
        data = json.load(stream)
    if data.get('timeout_origin') != 'actual_send':
        raise ValueError(f'{name}: incompatible request timeout; do not mix series')
    return data


def fmt(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'.replace('.', ',')


def pct(values, q=.99):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values)*q)-1)]


def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |',
            '| ' + ' | '.join('---' for _ in headers) + ' |'] + [
            '| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows]


def outcomes(rows):
    counts = dict(planned=len(rows), sent=sum(r[1] is not None for r in rows),
                  success=sum(200 <= r[3] < 300 for r in rows),
                  http_non2xx=sum(r[3] > 0 and not 200 <= r[3] < 300 for r in rows),
                  request_timeouts=sum(r[5] == 'timeout' for r in rows),
                  connection_errors=sum(r[5] == 'connection' for r in rows),
                  not_sent=sum(r[1] is None for r in rows))
    if counts['planned'] != sum(counts[k] for k in (
            'success', 'http_non2xx', 'request_timeouts', 'connection_errors', 'not_sent')):
        raise ValueError('Unknown request outcome in raw data')
    return counts


def augment(name):
    data, result = raw(name), read(name + '-summary')
    duration = int(data['duration'])
    bins = [[] for _ in range(duration)]
    sent = [0] * duration
    for row in data['rows']:
        done = int(row[2])
        if 0 <= done < duration:
            bins[done].append(row)
        if row[1] is not None and 0 <= int(row[1]) < duration:
            sent[int(row[1])] += 1
    result['sent'] = sent
    result['not_sent_per_second'] = [sum(r[1] is None for r in rows) for rows in bins]

    result['attempt_outcomes'] = outcomes(data['rows'])
    result['phase_outcomes'] = [dict(start=a, end=b, **outcomes([
        r for r in data['rows'] if a <= r[0] < b]))
        for a, b in ((0, 30), (30, 90), (90, duration))]
    result['completion_after_profile'] = sum(r[2] >= duration for r in data['rows'])
    result['clock_gaps'] = data.get('clock_gaps', [])
    result['request_timeout_seconds'] = data['request_timeout_seconds']
    result['timeout_origin'] = data['timeout_origin']
    result['latency_origin'] = data['latency_origin']
    (OUT / (name + '-summary.json')).write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return result


def save(fig, name):
    for ax in fig.axes:
        formatter = FuncFormatter(lambda value, position: format(value, 'g').replace('.', ','))
        ax.xaxis.set_major_formatter(formatter)
        ax.yaxis.set_major_formatter(formatter)
    fig.tight_layout()
    fig.savefig(FIG / (name + '.png'), dpi=160, facecolor='white')
    plt.close(fig)


def comparison(names, runs, filename):
    fig, axs = plt.subplots(5, 2, sharex=True, sharey='row', figsize=(13.5, 12))
    for j, name in enumerate(names):
        r, axes = runs[name], axs[:, j]
        t = range(r['T'])
        axes[0].set_title(name, fontweight='bold')
        axes[0].step(t, r['target'], where='post', label='Задано', color='#293c57')
        axes[0].plot(t, r['sent'], label='Відправлено', color='#df9b25', alpha=.8)
        axes[0].plot(t, r['achieved'], label='Успішно', color='#008879', alpha=.85)
        axes[1].plot(t, r['p99'], color='#b94751', label='p99 клієнта')
        axes[1].axhline(r['threshold'], color='black', ls='--', lw=1, label='Поріг порушення')
        axes[2].step(t, r['ready'], where='post', label='Готові s(t)', color='#008879')
        axes[2].step(t, r['demand'], where='post', label='Потрібні d(t)', color='#293c57', ls='--')
        if any(v is not None for v in r['desired']):
            axes[2].step(t, r['desired'], where='post', label='Бажані HPA', color='#b87420', ls=':')
        axes[3].plot(t, r['cpu'], color='#6a5ca6')
        axes[3].axhline(50, color='black', ls='--', lw=1, label='Ціль HPA 50%')
        axes[4].bar(t, r['not_sent_per_second'], color='#b94751', width=1)
        for ax, label in zip(axes, ('Запити/с', 'p99, мс', 'Репліки', 'CPU / request, %', 'Не відправлено/с')):
            ax.set_ylabel(label)
            ax.axvspan(30, 90, color='#eab65b', alpha=.13)
            ax.grid(alpha=.2)
        for i in (0, 1, 2, 3):
            axes[i].legend(fontsize=7, loc='upper right')
        axes[-1].set_xlabel('Час від початку прогону, с')
        axes[-1].set_xlim(0, r['T'])

    
    metrics = (
        lambda r: r['target'] + r['sent'] + r['achieved'],
        lambda r: r['p99'] + [r['threshold']],
        lambda r: r['ready'] + r['demand'] + r['desired'],
        lambda r: r['cpu'] + [50],
        lambda r: r['not_sent_per_second'])
    for row, values in enumerate(metrics):
        maximum = max((v for name in names for v in values(runs[name]) if v is not None), default=1)
        axs[row, 0].set_ylim(0, max(1, maximum) * 1.1)
    save(fig, filename)


def main():
    FIG.mkdir(exist_ok=True)
    points, fit = read('capacity'), read('usl')
    for p in points:
        raw('fixed-' + str(p['replicas']))
    runs = {name: augment(name) for name in NAMES}
    compose = json.loads(Path('compose-reference.json').read_text(encoding='utf-8-sig'))
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    for label, ps, model, color in (
            ('Compose', compose['points'], compose['model'], '#3376aa'),
            ('Kubernetes', points, fit, '#c06925')):
        base = ps[0]['x']
        ax.errorbar([p['replicas'] for p in ps], [p['x']/base for p in ps],
                    yerr=[p['std']/base for p in ps], fmt='o', capsize=4,
                    color=color, label=label + ': вимірювання')
        if model['valid']:
            ns = [1 + i/100 for i in range(301)]
            ax.plot(ns, [n/(1+model['sigma']*(n-1)+model['kappa']*n*(n-1)) for n in ns],
                    color=color, label=label + ': USL')
    ax.set(xlabel='Кількість реплік N', ylabel='C(N) = X(N) / X(1)', xticks=[1, 2, 3, 4])
    ax.grid(alpha=.2)
    ax.legend(fontsize=8)
    save(fig, 'usl-comparison')
    epoch = raw('fixed-4')['epoch']
    fig, ax = plt.subplots(figsize=(10, 4.8))
    colors, seen = {}, set()
    palette = plt.get_cmap('tab10')
    for step in points[-1]['steps']:
        for series in step['pod_series']:
            pod, samples = series['metric']['pod'], series['values']
            colors.setdefault(pod, palette(len(colors)))
            ax.plot([float(t)-epoch for t, _ in samples], [float(v) for _, v in samples],
                    color=colors[pod], marker='.', label=pod if pod not in seen else None)
            seen.add(pod)
    for boundary in (10, 40, 70):
        ax.axvline(boundary, ls='--', color='gray', lw=.7)
    ax.set(xlabel='Час від початку прогону N=4, с', ylabel='Запити/с на под (rate за 15 с)')
    ax.grid(alpha=.2)
    ax.legend(fontsize=7)
    save(fig, 'pod-rps')
    comparison(('static', 'hpa-30'), runs, 'elasticity')
    comparison(('d5-30', 'd5-300'), runs, 'd5')
    lines = ['# Числові результати лабораторної роботи 3', '',
             '## Фіксована кількість реплік', '']
    lines += table(['N', 'X(N), запитів/с', 'Розкид', 'Сходинка', 'p99 клієнта, мс',
                    'p99 сервера, мс', 'CFS середнє, %', 'CFS максимум, %', 'I'], [
        [p['replicas'], fmt(p['x']), fmt(p['std']), p['best']['target'], fmt(p['best']['p99']),
         fmt(p['best']['server']['value']*1000), fmt(p['best']['throttle_mean']*100),
         fmt(p['best']['throttle_max']*100), fmt(p['best']['imbalance'])] for p in points])
    lines += ['', '## Відправлення на кожній сходинці', '']
    lines += table(['N', 'Задано/с', 'Відправлено/с', 'Успішно/с', 'p95 відставання, мс',
                    'Не відправлено у вікні', 'HTTP не 2xx', 'Тайм-аути', 'Помилки з’єднання'], [
        [p['replicas'], s['target'], fmt(s['sent']), fmt(s['achieved']), fmt(s['lag_p95']),
         s['not_sent'], s['http_non2xx'], s['request_timeouts'], s['connection']]
        for p in points for s in p['steps']])
    lines += ['', '## Остання сходинка', '']
    lines += table(['N', 'Задано/с', 'Досягнуто/с', 'Досягнуто / задано', 'Критерій ≥ 0,9'], [
        [p['replicas'], p['steps'][-1]['target'], fmt(p['steps'][-1]['achieved']),
         fmt(p['steps'][-1]['achieved']/p['steps'][-1]['target']),
         'так' if p['generator_limited'] else 'ні'] for p in points])
    lines += ['', '## Зайнятість каналів генератора', '']
    channel_rows = []
    for p in points:
        data = raw('fixed-' + str(p['replicas']))
        a, b = p['best']['start'], p['best']['end']
        completed = [r for r in data['rows'] if r[1] is not None and a <= r[2] < b]
        active = [sum(r[1] is not None and r[1] <= a+i*.1 < r[2] for r in data['rows'])
                  for i in range(round((b-a)*10))]
        channel_rows.append([p['replicas'],
            fmt(statistics.mean((r[2]-r[1])*1000 for r in completed)),
            fmt(statistics.mean(active)), max(active)])
    lines += table(['N', 'Середній час після відправки, мс', 'Зайнято каналів у середньому',
                    'Максимум із 32'], channel_rows)
    lines += ['', 'Зайнятість відновлено за інтервалами [sent, done) кожні 0,1 с у вікні максимуму. '
              'Це опис виконаного прогону, а не окрема перевірка закону Літтла.']
    lines += ['', '## Крайні за навантаженням поди', '']
    lines += table(['N', 'Под', 'Запитів/с', 'p99 сервера, мс', 'CFS, %'], [
        [p['replicas'], v['pod'], fmt(p['best']['pod_rps'][v['pod']]),
         fmt(v['p99']['value']*1000), fmt(v['throttle']*100)]
        for p in points[1:] for v in p['best']['extremes']])
    lines += ['', '## Інтервали оцінки CFS', '']
    lines += table(['N', 'Под', 'Початок, с', 'Кінець, с', 'Тривалість, с', 'Періоди', 'Обмежені періоди'], [
        [p['replicas'], pod, fmt(e['start']-raw('fixed-'+str(p['replicas']))['epoch']),
         fmt(e['end']-raw('fixed-'+str(p['replicas']))['epoch']), fmt(e['end']-e['start']),
         int(e['periods']), int(e['throttled'])]
        for p in points for pod, e in p['best']['cfs_evidence'].items()])
    lines += ['', '## Порівняння з Compose', '']
    lines += table(['N', 'X Compose', 'C Compose', 'X Kubernetes', 'C Kubernetes'], [
        [p['replicas'], fmt(c['x']), fmt(c['x']/compose['points'][0]['x']),
         fmt(p['x']), fmt(p['x']/points[0]['x'])] for p, c in zip(points, compose['points'])])
    lines += ['', f"Kubernetes: σ = {fmt(fit['sigma'],6)}, κ = {fmt(fit['kappa'],6)}, N* = {fmt(fit['n_star'])}.",
              '', '![C(N) і моделі USL](figures/usl-comparison.png)', '',
              '![Запити на кожен под](figures/pod-rps.png)', '', '## Еластичність', '']
    lines += table(['Режим', 'T, с', 'θ_U', 'θ_O', 'a_U', 'a_O', 'W, под·с', 'Секунд понад поріг'], [
        [name, r['T'], fmt(r['theta_u']), fmt(r['theta_o']), fmt(r['a_u']), fmt(r['a_o']),
         fmt(r['W'],0), r['violations']] for name, r in runs.items()])
    lines += ['', '## Усі заплановані спроби профілів', '']
    fields = ('planned', 'sent', 'success', 'http_non2xx', 'request_timeouts', 'connection_errors', 'not_sent')
    lines += table(['Режим', 'Заплановано', 'Відправлено', '2xx', 'HTTP не 2xx', 'Тайм-аути',
                    'Помилки з’єднання', 'Не відправлено'], [
        [name] + [r['attempt_outcomes'][k] for k in fields] for name, r in runs.items()])
    lines += ['', 'Ця таблиця охоплює спроби за запланованим часом, включно із завершенням після T. '
              'Не відправлені спроби не є HTTP-відповідями. Графіки інтенсивності використовують час '
              'фактичної події, тому завершення запитів зі стрибка можуть потрапляти на початок спаду.',
              '', '## Спроби за фазами профілів', '']
    lines += table(['Режим', 'Фаза, с', 'Заплановано', '2xx', 'HTTP не 2xx', 'Тайм-аути', 'Не відправлено'], [
        [name, f"{p['start']}–{p['end']}", p['planned'], p['success'], p['http_non2xx'],
         p['request_timeouts'], p['not_sent']] for name, r in runs.items() for p in r['phase_outcomes']])
    lines += ['', '## Час реакції HPA', '']
    event = lambda v: 'не досягнуто' if v is None else str(v)
    lines += table(['Режим', 'Зміна desired, с', 'Максимум ready під час стрибка, с',
                    'Формальний критерій p99, с', 'Перше зменшення після стрибка, с'], [
        [name] + [event(r['timings'].get(k)) for k in ('desired_change', 'peak_ready', 'p99_recovery', 'scale_down')]
        for name, r in runs.items() if name != 'static'])
    lines += ['', 'Події виміряно від початку стрибка, крім зменшення — від його кінця. '
              'Крок спостереження метрик — 5 с. Значення 0 для критерію p99 означає лише перший '
              'десятисекундний відрізок нижче порогу; воно не гарантує збереження такого p99 далі.',
              '', f"Поріг порушення: {fmt(runs['static']['threshold'])} мс. Поріг критерію p99 HPA: "
              f"{fmt(1.2*runs['static']['jump_p99'])} мс.", '',
              '![Static і HPA](figures/elasticity.png)', '', '![Д5: два вікна](figures/d5.png)', '']
    Path('RESULTS.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
