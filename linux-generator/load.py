import asyncio
import math
import statistics
import time
from collections import Counter

import aiohttp


async def load(url, segments, stop_check=None):
    origin = time.perf_counter() + .5
    epoch = time.time() + .5
    scheduled = [[] for _ in range(32)]
    offset = seq = 0
    for duration, rate in segments:
        for k in range(math.ceil(duration * rate)):
            when = offset + k / rate
            if when < offset + duration:
                scheduled[seq % 32].append(when)
                seq += 1
        offset += duration

    rows = []
    cutoff = offset
    boundaries = {sum(d for d,_ in segments[:i]) for i in range(1,len(segments))}
    gates = {t: asyncio.Event() for t in boundaries} if stop_check else {}

    async def lane(times):
        timeout = aiohttp.ClientTimeout(total=5, ceil_threshold=1e9)
        for i in range(0, len(times), 100):
            async with aiohttp.ClientSession(
                timeout=timeout, connector=aiohttp.TCPConnector(limit=1, keepalive_timeout=120)
            ) as client:
                for when in times[i:i+100]:
                    while (delay := origin + when - time.perf_counter()) > 0:
                        await asyncio.sleep(delay)
                    if gates:
                        for boundary in sorted(gates):
                            if boundary > when:
                                break
                            if not gates[boundary].is_set():
                                await gates[boundary].wait()
                    if when >= cutoff:
                        return
                    sent = time.perf_counter() - origin
                    if sent >= when + 5:
                        rows.append([when, None, sent, 0, None, "timeout"])
                        continue
                    pod = error = None
                    status = 0
                    try:
                        async with client.get(url, timeout=aiohttp.ClientTimeout(total=5-(sent-when), ceil_threshold=1e9)) as res:
                            await res.read()
                            status, pod = res.status, res.headers.get('X-Pod')
                    except asyncio.TimeoutError:
                        error = 'timeout'
                    except aiohttp.ClientError:
                        error = 'connection'
                    rows.append([when, sent, time.perf_counter()-origin, status, pod, error])

    async def observe():
        nonlocal cutoff
        end = 0
        for duration, rate in segments:
            end += duration
            await asyncio.sleep(max(0, origin+end-time.perf_counter()))
            if stop_check and stop_check(rows, end, rate):
                cutoff = end
                for gate in gates.values():
                    gate.set()
                break
            if end in gates:
                gates[end].set()

    gaps = []
    async def watch_clock():
        last = time.perf_counter()
        wall = time.time()
        while True:
            await asyncio.sleep(.5)
            now, new_wall = time.perf_counter(), time.time()
            gap = max(now-last, new_wall-wall)
            if gap > 3:
                gaps.append({'second': now-origin, 'gap_seconds': gap})
            last, wall = now, new_wall

    watchdog = asyncio.create_task(watch_clock())
    tasks = [asyncio.create_task(lane(q)) for q in scheduled]
    try:
        if stop_check:
            await observe()
        await asyncio.gather(*tasks)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
    return {'epoch': epoch, 'segments': segments, 'rows': sorted(r for r in rows if r[0]<cutoff),
            'duration':cutoff, 'clock_gaps':gaps}


def percentile(values, p=.99):
    return sorted(values)[max(0, math.ceil(len(values)*p)-1)] if values else None


def summarize(rows, start, end):
    selected = [r for r in rows if start <= r[2] < end]
    sent = [r for r in rows if r[1] is not None and start <= r[1] < end]
    planned = [r for r in rows if start <= r[0] < end]
    good = Counter(int(r[2]) for r in selected if 200 <= r[3] < 300)
    seconds = [good[t] for t in range(start,end)]
    return dict(start=start,end=end,planned=len(planned)/(end-start),achieved=statistics.mean(seconds),
                std=statistics.stdev(seconds) if len(seconds)>1 else 0,
                sent=len(sent)/(end-start),
                lag_p95=percentile([1000*(r[1]-r[0]) for r in planned if r[1] is not None], .95),
                p99=percentile([1000*(r[2]-r[0]) for r in selected]),
                non2xx=sum(not 200<=r[3]<300 and r[5]!='connection' for r in selected),
                connection=sum(r[5]=='connection' for r in selected),
                pods=dict(Counter(r[4] for r in selected if r[4])))
