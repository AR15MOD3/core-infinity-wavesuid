"""Service-local media recency and bounded, self-contained PNG reuse.

No source mtime is used as an access clock. External/unknown active resources
are conservatively rendered again; no credentials or card text enter metadata.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hashlib
from html.parser import HTMLParser
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import weakref

RENDER_REVISION = 'png-self-contained-v1-1600-1200-600-400'
MAX_RENDER_AGE = 900
MAX_SOURCE = 16 * 1024 * 1024
_guard = threading.RLock()
_locks = weakref.WeakKeyDictionary()

def atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _meta(path: Path, kind: str) -> Path:
    return path.parent / '.media-cache-v1' / (path.name + '.' + kind + '.json')

def _read(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 4096: return {}
        row = json.loads(path.read_bytes())
        return row if isinstance(row, dict) and row.get('version') == 1 else {}
    except (OSError, ValueError, TypeError): return {}

def _write(path: Path, row: dict) -> None:
    if path.parent.is_symlink() or path.is_symlink(): raise ValueError('linked media metadata')
    atomic_bytes(path, json.dumps({'version': 1, **row}, separators=(',', ':')).encode())

def last_use(path: Path, fallback: float) -> float:
    value = _read(_meta(path, 'use')).get('at')
    now = time.time()
    if type(value) not in (int, float) or not 0 <= value <= now + 1: return fallback
    return max(fallback, value)

def remember_access(path: Path, interval: float = 60) -> None:
    try:
        with _guard:
            stamp = path.stat().st_mtime
            if time.time() - last_use(path, stamp) >= interval:
                _write(_meta(path, 'use'), {'at': time.time()})
    except (OSError, ValueError):
        pass  # recency diagnostics/metadata failure never changes source bytes

def remove_metadata(path: Path) -> None:
    for kind in ('use', 'render'):
        try: _meta(path, kind).unlink(missing_ok=True)
        except OSError: pass

def prune_metadata(root: Path) -> None:
    meta = root / '.media-cache-v1'
    if meta.is_symlink() or not meta.is_dir(): return
    for item in meta.iterdir():
        for suffix in ('.use.json', '.render.json'):
            if item.name.endswith(suffix):
                original = root / item.name[:-len(suffix)]
                if not original.is_file():
                    try: item.unlink()
                    except OSError: pass
                break

class _Resources(HTMLParser):
    def __init__(self):
        super().__init__(); self.safe = True; self.scripts = []; self.in_script = False
    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('iframe', 'object', 'embed', 'base', 'video', 'audio'):
            self.safe = False
        if tag.lower() == 'script':
            self.in_script = True; self.scripts.append('')
        for key, value in attrs:
            key = key.lower(); value = (value or '').strip()
            if key.startswith('on') or key in ('srcset', 'poster'): self.safe = False
            if key in ('src', 'href', 'xlink:href', 'data') and value and not value.startswith(('data:', '#')):
                self.safe = False
    def handle_data(self, data):
        if self.in_script: self.scripts[-1] += data
    def handle_endtag(self, tag):
        if tag.lower() == 'script': self.in_script = False

def signature(source: Path) -> str | None:
    try:
        if source.is_symlink() or source.stat().st_size > MAX_SOURCE: return None
        raw = source.read_bytes(); text = raw.decode('utf-8')
        resources = _Resources(); resources.feed(text)
        if not resources.safe or re.search(r'@import\b', text, re.I): return None
        for ref in re.findall(r'url\(\s*([^)]+)\)', text, re.I):
            if not ref.strip(' \t\r\n\'"').startswith(('data:', '#')): return None
        if resources.scripts:
            # Only the service's reviewed layout/action bridge is deterministic.
            from rover.html_actions import _BRIDGE
            approved = re.findall(r'<script[^>]*>(.*?)</script>', _BRIDGE, re.S | re.I)
            if len(resources.scripts) != 1 or len(approved) != 1 or resources.scripts[0].strip() != approved[0].strip():
                return None
        try: runtime = version('playwright')
        except PackageNotFoundError: runtime = 'unavailable'
        return hashlib.sha256(RENDER_REVISION.encode() + runtime.encode() + raw).hexdigest()
    except (OSError, ValueError, ImportError): return None

def valid_png(source: Path, target: Path, fingerprint: str | None) -> bool:
    if fingerprint is None: return False
    try:
        if target.is_symlink() or not target.is_file(): return False
        row = _read(_meta(target, 'render'))
        stamp = row.get('at')
        return (row.get('signature') == fingerprint and type(stamp) in (int, float)
                and 0 <= time.time() - stamp < MAX_RENDER_AGE
                and row.get('png_sha256') == hashlib.sha256(target.read_bytes()).hexdigest())
    except OSError: return False

def remember_png(target: Path, fingerprint: str | None) -> None:
    if fingerprint is None:
        try: _meta(target, 'render').unlink(missing_ok=True)
        except OSError: pass
        return
    try:
        _write(_meta(target, 'render'), {'at': time.time(), 'signature': fingerprint,
                                       'png_sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    except (OSError, ValueError): pass  # next request cold-renders safely

@asynccontextmanager
async def render_lock(source: Path):
    loop = asyncio.get_running_loop()
    with _guard:
        locks = _locks.setdefault(loop, weakref.WeakValueDictionary())
        key = str(source.resolve())
        lock = locks.get(key)
        if lock is None: lock = asyncio.Lock(); locks[key] = lock
    async with lock:
        yield
