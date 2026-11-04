"""Credential-free scoped installer. No downloads, process starts, or overwrites.

Upgrade into a new versioned service directory. Preserve the existing scoped
configuration byte-for-byte; changing serviceDir is an explicit operator step.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


def files(package):
    service = package / 'service'
    denied = {'.git', '.venv', '__pycache__', 'data', 'logs', '.pytest_cache'}
    for path in sorted(service.rglob('*')):
        rel = path.relative_to(service)
        if any(part in denied for part in rel.parts) or not path.is_file():
            continue
        if path.is_symlink() or path.name == 'config.json' or path.suffix in ('.log', '.pyc', '.db', '.sqlite'):
            continue
        yield rel, path.read_bytes()


def exclusive_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.waves-stage-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        # Atomic publication without replacing a concurrent writer's file.
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def provision(package, service, config, owner, data, port, python, *, apply=False, write_config=False):
    package, service, config, data, python = map(Path, (package, service, config, data, python))
    if not owner or any(c in owner for c in '/\\:'):
        raise ValueError('explicit owner Bot ID required')
    for path in (package, service, config, data, python):
        if not path.is_absolute() or path.resolve() != path.absolute():
            raise ValueError('absolute non-redirected paths required')
    if (config.name != 'config.json' or config.parent.name != 'core-infinity-wavesuid'
            or config.parent.parent.name != 'plugins'
            or config.parent.parent.parent.name != 'infinity'
            or config.parent.parent.parent.parent.name != owner):
        raise ValueError('config must be bots/<owner>/infinity/plugins/core-infinity-wavesuid/config.json')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('explicit dedicated port required')
    if config.parent/'runtime' not in data.parents:
        raise ValueError('data must be below current Bot plugin runtime resource directory')
    for code in (package, service):
        if data == code or code in data.parents or data in code.parents:
            raise ValueError('code and account data must be separate')
    if service == package or package in service.parents or service in package.parents:
        raise ValueError('use external versioned service directory')
    payload = list(files(package))
    for required in ('scoped_main.py', 'scope_identity.py', 'rover/main.py', 'rover/data_store.py'):
        if not any(str(rel).replace('\\', '/') == required for rel, _ in payload):
            raise ValueError('missing service payload ' + required)
    for rel, raw in payload:
        dst = service / rel
        if service not in dst.resolve().parents:
            raise ValueError('destination escaped service root')
        if dst.exists() and (not dst.is_file() or dst.read_bytes() != raw):
            raise ValueError('existing code differs; use a new versioned service directory: ' + str(rel))
    existing = config.read_bytes() if config.exists() else None
    if existing is not None:
        cfg = json.loads(existing.decode('utf-8-sig'))
        if not isinstance(cfg, dict) or cfg.get('ownerBot') != owner:
            raise ValueError('existing configuration owner mismatch')
    result = {'files': len(payload), 'configuration': 'preserved' if existing is not None else 'planned',
              'downloads': False, 'started': False, 'applied': apply}
    if apply:
        for rel, raw in payload:
            dst = service / rel
            if not dst.exists(): exclusive_atomic(dst, raw)
            elif dst.read_bytes() != raw: raise ValueError('concurrent service change')
        if write_config and existing is None:
            cfg = json.loads((package / 'config.example.json').read_text(encoding='utf-8-sig'))
            cfg.update(ownerBot=owner, dataDir=str(data), serviceDir=str(service), port=port,
                       python=str(python), autoStart=True)
            exclusive_atomic(config, (json.dumps(cfg, ensure_ascii=False, indent=2) + '\n').encode())
            result['configuration'] = 'created'
        elif existing is not None and config.read_bytes() != existing:
            raise ValueError('concurrent configuration change; no overwrite performed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('service-dir', 'config-path', 'owner', 'data-dir', 'python'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--write-config', action='store_true')
    args = parser.parse_args()
    try:
        result = provision(Path(__file__).resolve().parents[1], args.service_dir, args.config_path,
                           args.owner, args.data_dir, args.port, args.python,
                           apply=args.apply, write_config=args.write_config)
    except (OSError, ValueError) as exc:
        parser.exit(2, 'provision failed: ' + str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
