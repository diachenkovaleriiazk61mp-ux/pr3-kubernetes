import asyncio
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import platform
import urllib.request

from load import load, summarize, percentile


def main():
    ids = json.loads(Path('/data/results/seed.json').read_text(encoding='utf-8-sig'))
    ident = ids[0]
    out = Path('/data/results/diagnostics')
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    results = []
    paths = [('nodeport', 'http://pr3-control-plane:30080'),
             ('published', 'http://host.docker.internal:8080')]
    for name, base in paths:
        url = f'{base}/vaccinations/{ident}'
        print(f'Checking {name}: {base}', flush=True)
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                if response.status != 200:
                    raise RuntimeError(f'Preflight HTTP {response.status}')
            data = asyncio.run(load(url, [(30, 200)]))
            data.update(generator_platform=platform.platform(), url=url,
                        diagnostic_only=True)
            raw_path = out / f'linux-{name}-{stamp}.json.gz'
            with gzip.open(raw_path, 'wt', encoding='utf-8') as stream:
                json.dump(data, stream, allow_nan=False)
            stats = summarize(data['rows'], 10, 30)
            actual = [1000*(r[2]-r[1]) for r in data['rows']
                      if r[1] is not None and r[3] == 200]
            stats.update(path=name, url=url, raw_file=raw_path.name,
                         statuses=dict(Counter(f'{r[3]}/{r[5]}' for r in data['rows'])),
                         successful_after_send_p95_ms=percentile(actual, .95),
                         timed_out_before_sending=sum(r[1] is None for r in data['rows']),
                         clock_gaps=data.get('clock_gaps', []))
            results.append(stats)
            print(json.dumps(stats, indent=2), flush=True)
        except Exception as exc:
            result = dict(path=name, error=f'{type(exc).__name__}: {exc}')
            results.append(result)
            print(json.dumps(result, indent=2), flush=True)
    summary = out / f'linux-probe-{stamp}.json'
    summary.write_text(json.dumps(results, indent=2, allow_nan=False), encoding='utf-8')
    print(f'Saved: results/diagnostics/{summary.name}', flush=True)
    if not any('achieved' in r for r in results):
        raise SystemExit('Neither route was available; no load measurement completed.')


if __name__ == '__main__':
    main()
