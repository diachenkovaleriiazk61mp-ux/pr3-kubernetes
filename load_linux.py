import asyncio
import json
from pathlib import Path
import uuid
from urllib.parse import urlsplit, urlunsplit


def container_url(url):
    parsed = urlsplit(url)
    if parsed.hostname not in ('localhost', '127.0.0.1') or parsed.port != 8080:
        raise ValueError('Linux backend expects the published API port localhost:8080')
    return urlunsplit((parsed.scheme, 'host.docker.internal:8080', parsed.path,
                       parsed.query, parsed.fragment))


async def load_linux(url, segments, stop_check=None):
    policy = getattr(stop_check, 'linux_policy', None) if stop_check else None
    if stop_check is not None and policy != 'capacity90':
        raise ValueError('This stop callback has no Linux equivalent')
    root = Path(__file__).resolve().parent
    name = 'pr3-load-' + uuid.uuid4().hex[:12]
    payload = dict(url=container_url(url), segments=segments, policy=policy,
                   replicas=getattr(stop_check, 'replicas', None))
    process = await asyncio.create_subprocess_exec(
        'docker', 'run', '--rm', '-i', '--name', name, '--network', 'kind',
        '--mount', f'type=bind,source={root},target=/data,readonly',
        '--workdir', '/data', '--entrypoint', 'python',
        'pr3-generator-probe:local', '/data/linux-generator/worker.py',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    try:
        output, _ = await process.communicate(json.dumps(payload).encode('utf-8'))
        if process.returncode:
            raise RuntimeError(f'Linux generator failed with exit code {process.returncode}')
        data = json.loads(output)
        if data.get('generator_backend') != 'docker-linux':
            raise RuntimeError('Linux generator returned an unexpected payload')
        return data
    except BaseException:
        if process.returncode is None:
            cleanup = await asyncio.create_subprocess_exec(
                'docker', 'rm', '-f', name, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.shield(cleanup.wait())
            await asyncio.shield(process.wait())
        raise
