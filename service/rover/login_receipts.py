"""Short-lived browser access only for login URLs issued by owned QQ jobs."""
import contextvars
import functools
import os
import re
import sys
import time
from urllib.parse import urlsplit

current_job = contextvars.ContextVar('waves_login_job', default=None)
issued = {}
TOKEN = re.compile(r'[A-Za-z0-9_-]{16,128}\Z')
POSTS = frozenset(('/waves/login', '/waves/c/sendCode', '/waves/c/login', '/waves/l/login', '/waves/l/bind'))

def qq_job(job):
    if job is None or not isinstance(job.owner, tuple) or len(job.owner) != 3:
        return False
    user, scene, audience = job.owner
    if not all(isinstance(x, str) and x for x in (user, scene, audience)):
        return False
    return ((scene == 'direct' and audience == 'private:qq:' + user)
            or (scene == 'group' and audience.startswith('group:qq:')
                and bool(audience[len('group:qq:'):])))

def register(url):
    job = current_job.get()
    if not qq_job(job):
        return
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1'):
        return
    try:
        from rover.config import core_config
        if parsed.port != int(core_config.get_config('PORT')): return
    except (ValueError, TypeError): return
    prefix = '/waves/i/'
    if not parsed.path.startswith(prefix) or parsed.query or parsed.fragment: return
    token = parsed.path[len(prefix):]
    if not TOKEN.fullmatch(token): return
    now = time.monotonic()
    for key, (owner, deadline) in list(issued.items()):
        if owner.done or deadline <= now: issued.pop(key, None)
    if len(issued) >= 64: return
    issued[token] = (job, now + 180)

def install():
    # Wrap one reviewed entry point. No database inspection or credential copying.
    name = 'plugins.XutheringWavesUID.XutheringWavesUID.wutheringwaves_login.login'
    module = sys.modules.get(name)
    original = getattr(module, 'send_login', None)
    if not callable(original) or getattr(original, '__receipt_entry__', False): return
    @functools.wraps(original)
    async def send_login(bot, ev, url, *args, **kwargs):
        register(url)
        return await original(bot, ev, url, *args, **kwargs)
    send_login.__receipt_entry__ = True
    prefix = 'plugins.XutheringWavesUID.XutheringWavesUID.'
    for key, loaded in tuple(sys.modules.items()):
        if key.startswith(prefix) and loaded is not None:
            for field, value in tuple(vars(loaded).items()):
                if value is original:
                    setattr(loaded, field, send_login)

async def browser_allowed(request):
    token = None
    if request.method == 'GET' and request.url.path.startswith('/waves/i/'):
        token = request.url.path[len('/waves/i/'):]
    elif request.method == 'POST' and request.url.path in POSTS:
        # Browser credential submissions must originate at the very same local service.
        origin = request.headers.get('origin', '')
        if origin != str(request.base_url).rstrip('/'): return False
        if request.headers.get('sec-fetch-site') not in (None, 'same-origin'): return False
        try:
            size = int(request.headers.get('content-length', '0'))
            if not 0 < size <= 16384: return False
            raw = await request.body()
            if len(raw) > 16384: return False
            import json
            payload = json.loads(raw)
            token = payload.get('auth') if isinstance(payload, dict) else None
        except (ValueError, TypeError): return False
    if not isinstance(token, str) or not TOKEN.fullmatch(token): return False
    entry = issued.get(token)
    if entry is None: return False
    job, deadline = entry
    if job.done or time.monotonic() >= deadline:
        issued.pop(token, None); return False
    return qq_job(job)
