import asyncio
import json
import platform
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from load import _load_native, summarize


def make_stop(policy, replicas):
    if policy is None:
        return None
    if policy != 'capacity90':
        raise ValueError('Unknown stop policy')
    consecutive = 0
    def stop(rows, end, rate):
        nonlocal consecutive
        if end == 10:
            return False
        stat = summarize(rows, end-20, end)
        consecutive = consecutive+1 if stat['achieved'] < .9*rate else 0
        print('N', replicas, 'target', rate, 'sent', stat['sent'],
              'achieved', stat['achieved'], file=sys.stderr, flush=True)
        return consecutive >= 2
    return stop


def main():
    request = json.load(sys.stdin)
    stop = make_stop(request.get('policy'), request.get('replicas'))
    result = asyncio.run(_load_native(request['url'], request['segments'], stop))
    result.update(generator_backend='docker-linux',
                  generator_platform=platform.platform(),
                  generator_python=platform.python_version(),
                  url=request['url'])
    json.dump(result, sys.stdout, allow_nan=False)


if __name__ == '__main__':
    main()
