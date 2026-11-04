"""Per-instance bridge runtime. Local plugins are trusted code, not a sandbox."""
import hashlib
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BRIDGE_VERSION='0.3.1'


def fingerprint(owner, data):
    return hashlib.sha256((owner + '\0' + os.path.normcase(str(Path(data).resolve()))).encode()).hexdigest()


def local_json(url, payload=None, timeout=5, token=''):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json', 'X-Waves-Instance-Token': token})
    # No ambient proxy, redirects, or implicit retries for state-changing commands.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('service response too large')
        return json.loads(raw)


class ScopedRuntime:
    def __init__(self, ctx, read_config, log):
        self.ctx, self.read_config, self.log = dict(ctx), read_config, log
        self.lock = threading.RLock()
        self.proc = None
        self.settings = None
        self.token = ''
        self.last_error = ''
        self.stopping = threading.Event()
        self.supervisor = None
        self.restart_count = 0
        self._public_cache = {}  # instance-local; no account commands or user data
        self.command_prefixes = ()

    def validated(self):
        cfg, problems = self.read_config()
        if problems:
            raise ValueError('配置错误：' + '；'.join(problems))
        owner = self.ctx.get('bot_id')
        if not isinstance(owner, str) or not owner.strip() or cfg.get('ownerBot') != owner:
            raise ValueError('宿主 bot_id 与 ownerBot 必须明确一致，禁止沿用其他 Bot 服务')
        if cfg.get('host', '127.0.0.1') not in ('127.0.0.1', 'localhost'):
            raise ValueError('服务地址必须是本机')
        port = cfg.get('port')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('端口必须是 1024..65535 整数')
        timeout = cfg.get('commandTimeout', 90)
        if type(timeout) not in (int, float) or not 5 <= timeout <= 600:
            raise ValueError('commandTimeout 必须在 5..600 秒内')
        if cfg.get('autoStart') is not True:
            raise ValueError('隔离模式只允许 autoStart=true 的自有服务，不接管手动服务')
        data = Path(str(cfg.get('dataDir') or ''))
        service = Path(str(cfg.get('serviceDir') or ''))
        python = Path(str(cfg.get('python') or ''))
        if not data.is_absolute() or not service.is_absolute() or not python.is_absolute():
            raise ValueError('dataDir、serviceDir 和 python 必须为明确的绝对路径')
        data, service = data.resolve(), service.resolve()
        if cfg.get('_scopeDir'):
            scope = Path(cfg['_scopeDir']).resolve()
            if data == scope or scope not in data.parents:
                raise ValueError('dataDir 必须位于当前 Bot 的 wavesuid 独立目录内')
        package = Path(self.ctx['packageDir']).resolve()
        if data == service or service in data.parents or data in service.parents:
            raise ValueError('运行数据与服务代码必须分离，禁止共用 service/data')
        if data == package or package in data.parents or data in package.parents:
            raise ValueError('运行数据与插件包必须分离，避免市场更新覆盖')
        if not python.is_file() or not (service / 'scoped_main.py').is_file():
            raise ValueError('缺少解释器或隔离服务入口 scoped_main.py')
        cfg = dict(cfg, ownerBot=owner, dataDir=str(data), serviceDir=str(service), python=str(python))
        cfg['scopeId'] = fingerprint(owner, data)
        cfg['base'] = f'http://127.0.0.1:{port}'
        return cfg

    def _health(self, cfg):
        try:
            return local_json(cfg['base'] + '/api/health', timeout=0.7)
        except (OSError, ValueError):
            return None

    def _matches(self, health):
        return (isinstance(health, dict) and health.get('ok') is True
                and health.get('scope_id') == self.settings['scopeId']
                and health.get('instance_id') == hashlib.sha256(self.token.encode()).hexdigest()
                and health.get('bridge_version') == BRIDGE_VERSION
                and self.proc.owns_pid(health.get('pid')))

    def start(self):
        with self.lock:
            if self.supervisor is not None and self.supervisor.is_alive():
                return  # never reset a live supervisor or its bounded budget
            if self.proc is not None:
                if self.proc.poll() is None:
                    return
                self._stop_owned()
            self.settings = self.validated()
            self.stopping.clear()
            self.restart_count = 0
            self._launch_owned()
            self.supervisor = threading.Thread(target=self._supervise,
                name='waves-owned-service-supervisor', daemon=True)
            try:
                observe=self.ctx.get('start_background_thread')
                if callable(observe):
                    self._supervisor_receipt=observe(self.supervisor,stop=self.stopping.set)
                else:self.supervisor.start()
            except BaseException:
                self.stopping.set()
                self._stop_owned()
                self.supervisor = None
                raise

    def _launch_owned(self):
        # Called with lock held. Restart uses frozen settings, a NEW token and
        # NEW owned Job/process group; no command replay and no foreign adoption.
        from telyincore.plugins import _require_unconfined_lifecycle
        _require_unconfined_lifecycle()  # background recovery grants no new authority
        # Any listener, even same Bot, belongs to somebody else until this
        # instance actually creates and retains its own Popen handle.
        if self._health(self.settings) is not None:
            raise RuntimeError('端口已有服务，拒绝沿用或接管')
        self._public_cache.clear()
        self.command_prefixes = ()
        self.token = secrets.token_hex(32)
        env = {k: v for k, v in os.environ.items() if not k.startswith('ROVER_')}
        env.update(ROVER_DATA_DIR=self.settings['dataDir'], ROVER_OWNER_BOT=self.settings['ownerBot'],
                   ROVER_SCOPE_ID=self.settings['scopeId'], ROVER_INSTANCE_TOKEN=self.token,
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        reference=self.ctx.get('background_evidence_reference')
        reference=reference() if callable(reference) else None
        if reference is not None:
            env.update(ROVER_TEV_SOURCE=reference['source'],ROVER_TEV_SHA256=reference['sha256'])
        command = [self.settings['python'], '-m', 'scoped_main', '--host', '127.0.0.1', '--port', str(self.settings['port'])]
        import importlib.util
        spec = importlib.util.spec_from_file_location('waves_owned_child', Path(__file__).with_name('owned_child.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.proc = module.OwnedChild(command, popen=subprocess.Popen, cwd=self.settings['serviceDir'], env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.stopping.is_set():
                    raise RuntimeError("service start cancelled by plugin stop")
                if self.proc.poll() is not None:
                    raise RuntimeError(f'隔离服务启动退出：{self.proc.returncode}')
                health = self._health(self.settings)
                if health is not None:
                    if not self._matches(health):
                        raise RuntimeError('服务实例或数据归属不匹配')
                    prefixes = health.get('prefixes')
                    if not isinstance(prefixes, list) or not all(isinstance(v, str) for v in prefixes):
                        raise RuntimeError('owned service command prefixes missing')
                    self.command_prefixes = tuple(v for v in prefixes if v)
                    self.last_error = ''
                    return
                self.stopping.wait(0.1)
            raise TimeoutError('隔离服务启动超时')
        except BaseException:
            self._stop_owned()
            raise


    def _supervise(self):
        try:
            while not self.stopping.wait(0.2):
                with self.lock:
                    if self.stopping.is_set() or self.proc is None:
                        return
                    code = self.proc.poll()
                    if code is None:
                        continue
                    # Close the OLD owned process tree/Job before creating a new
                    # one. Cleanup failures retain the handle, and never respawn.
                    self._stop_owned()
                    if code != 75:
                        self.last_error = f'服务退出 {code}；非重启请求，未自动重试'
                        self.log(self.last_error)
                        return
                    if self.restart_count >= 3:
                        self.last_error = '服务连续请求重启；已达本次启用3次上限，请检查日志后重新启用'
                        self.log(self.last_error)
                        return
                    self.restart_count += 1
                    delay = min(2 ** (self.restart_count - 1), 4)
                # Wait outside lock, cancellable immediately by stop().
                if self.stopping.wait(delay):
                    return
                with self.lock:
                    if self.stopping.is_set():
                        return
                    self._launch_owned()
                    self.log(f'自有服务退出75后恢复：{self.restart_count}/3；未重放命令')
        except Exception as exc:
            with self.lock:
                self.last_error = '受控恢复失败：' + type(exc).__name__
            self.log(self.last_error)

    def _stop_owned(self):
        proc = self.proc
        # Retain ownership if shutdown cannot be confirmed; never lose the
        # handle and then accidentally launch a second copy.
        if proc is not None:
            requested=False;forced=False
            if proc.poll() is None:
                try:
                    health=self._health(self.settings)
                    if not self._matches(health):raise RuntimeError('owned stop identity mismatch')
                    reply=local_json(self.settings['base']+'/api/owned-stop',{},timeout=2,token=self.token)
                    requested=isinstance(reply,dict) and reply.get('accepted') is True
                except Exception:
                    requested=False
                if not requested:
                    forced=True;proc.terminate()
            try:proc.wait(10)
            except subprocess.TimeoutExpired:
                forced=True;proc.kill();proc.wait(5)
            self.last_shutdown={'normal_stop_requested':requested,'forced':forced,
                                'task_drain_proven':False}
        self.proc = None
        self.token = ''
        self.command_prefixes = ()
        self._public_cache.clear()

    def stop(self):
        self.stopping.set()  # cancellation must precede waiting for startup lock
        with self.lock:
            self._stop_owned()
            supervisor = self.supervisor
        if supervisor is not None and supervisor is not threading.current_thread():
            supervisor.join(5)
            if supervisor.is_alive():
                raise RuntimeError('service supervisor stop incomplete')
        with self.lock:
            if self.supervisor is supervisor:
                self.supervisor = None

    def _timing(self, label, started):
        # Fixed labels and milliseconds only: no commands, identities, URLs or tokens.
        try:
            self.log(f'waves timing {label}={max(0, round((time.monotonic()-started)*1000))}ms')
        except Exception:
            pass  # optional diagnostics must not turn a completed command into failure

    def request(self, path, payload=None, timeout=None, *, _cancelled=None):
        entered = time.monotonic()
        with self.lock:
            self._timing('lock_wait', entered)
            if self.stopping.is_set() or (_cancelled is not None and _cancelled.is_set()):
                raise RuntimeError('owned query cancelled before health I/O')
            if self.proc is None or self.proc.poll() is not None:
                self._public_cache.clear()
                raise RuntimeError('当前插件没有已就绪的自有服务')
            request_snapshot = (self.proc, self.token, self.ctx.get('bot_id'),
                                self.settings.get('scopeId'), self.settings.get('base'))
            started = time.monotonic()
            health = self._health(self.settings)
            if self.stopping.is_set() or (_cancelled is not None and _cancelled.is_set()):
                raise RuntimeError('owned query cancelled before endpoint I/O')
            self._assert_generation(request_snapshot, _cancelled)
            trusted = self._matches(health)
            self._timing('identity_check', started)
            if not trusted:
                self._public_cache.clear()
                raise RuntimeError('服务实例已改变，拒绝跨 Bot 调用')
            if not isinstance(path, str) or not path.startswith('/api/') or '..' in path:
                raise ValueError('无效的服务路径')
            # Exactly two public endpoints. Any user/image/extra field forbids cache.
            public = (path == '/api/commands' and payload is None) or (
                path == '/api/chat' and isinstance(payload, dict)
                and set(payload) == {'command', 'scene'}
                and payload.get('command') == 'ww帮助'
                and payload.get('scene') == self.settings.get('scene', 'group'))
            key = (self.token, health.get('bridge_version'), health.get('commands'),
                   path, json.dumps(payload, sort_keys=True, ensure_ascii=False)) if public else None
            if self.stopping.is_set() or (_cancelled is not None and _cancelled.is_set()):
                raise RuntimeError('owned query cancelled before endpoint I/O')
            cached = self._public_cache.get(key) if public else None
            if cached is not None and time.monotonic() - cached[0] < 60:
                self._timing('public_cache_hit_total', entered)
                return copy.deepcopy(cached[1])
            started = time.monotonic()
            try:
                answer = local_json(self.settings['base'] + path, payload,
                                    timeout or self.settings['commandTimeout'], self.token)
            except BaseException:
                self._timing('request_failed', started)
                raise
            if self.stopping.is_set() or (_cancelled is not None and _cancelled.is_set()):
                raise RuntimeError('owned query cancelled; result unconfirmed')
            self._assert_generation(request_snapshot, _cancelled)
            self._timing('public_query' if public else 'uncached_request', started)
            cacheable = isinstance(answer, dict) and (
                answer.get('ok') is True or (path == '/api/commands'
                and answer.get('ok') is not False and isinstance(answer.get('commands'), list)))
            if public and cacheable:
                # Bounded two-entry cache; refreshes never retry a failed command.
                self._public_cache = {k:v for k,v in self._public_cache.items()
                                      if k[0] == self.token and time.monotonic()-v[0] < 60}
                if len(self._public_cache) >= 2 and key not in self._public_cache:
                    self._public_cache.pop(next(iter(self._public_cache)))
                self._public_cache[key] = (time.monotonic(), copy.deepcopy(answer))
            self._timing('request_total', entered)
            return answer

    def status(self):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                return 'waves 隔离服务未运行；' + (self.last_error or '请检查专属配置并启用插件')
            health = self._health(self.settings)
            if not self._matches(health):
                return 'waves 隔离服务身份校验失败，已拒绝调用'
            return f"waves service 在线：Bot={self.settings['ownerBot']}；版本={health['bridge_version']}；命令 {health.get('commands')} 条；自有进程=是"


    def prepare_status(self):
        """Freeze this instance on the owner thread; never wait for a busy lock."""
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('status instance is busy; no I/O started')
        try:
            if self.stopping.is_set():
                raise RuntimeError('status instance is stopping; no I/O started')
            if self.proc is None or self.proc.poll() is not None:
                return 'waves 隔离服务未运行；' + (
                    self.last_error or '请检查专属配置并启用插件')
            cfg = self.settings
            owner = self.ctx.get('bot_id')
            if (not cfg or not isinstance(owner, str) or not owner
                    or cfg.get('ownerBot') != owner or not cfg.get('scopeId')
                    or not self.token):
                raise RuntimeError('status owner/scope is not valid')
            # Immutable values plus exact process identity. No ambient active
            # events, current Bot selector, or model-supplied account is captured.
            return (self.proc, self.token, owner, cfg['scopeId'], cfg['base'])
        finally:
            self.lock.release()


    def status_for(self, snapshot, cancelled):
        """Worker-only check against the frozen instance before any HTTP call."""
        proc, token, owner, scope, base = snapshot
        with self.lock:
            cfg = self.settings
            if (cancelled.is_set() or self.stopping.is_set() or self.proc is not proc
                    or self.token != token or not cfg or cfg.get('ownerBot') != owner
                    or cfg.get('scopeId') != scope or cfg.get('base') != base
                    or self.ctx.get('bot_id') != owner):
                raise RuntimeError('status snapshot expired; no I/O started')
            if proc.poll() is not None:
                return 'waves 隔离服务未运行；' + (
                    self.last_error or '请检查专属配置并启用插件')
            health = self._health({'base': base})
            self._assert_generation(snapshot, cancelled)
            if not self._matches(health):
                return 'waves 隔离服务身份校验失败，已拒绝调用'
            return (f"waves service 在线：Bot={owner}；版本={health['bridge_version']}；"
                    f"命令 {health.get('commands')} 条；自有进程=是")


    def prepare_public(self):
        """Owner-thread, nonblocking capture of public query rendering/settings."""
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('public instance is busy; no I/O started')
        try:
            snapshot = self.prepare_status()
            if isinstance(snapshot, str):
                raise RuntimeError('public instance is offline; no I/O started')
            cfg = self.settings
            return (snapshot, str(cfg.get('scene') or 'group'),
                    float(cfg.get('commandTimeout') or 90),
                    str(cfg.get('mediaFormat') or 'png'))
        finally:
            self.lock.release()


    def public_request_for(self, frozen, path, payload, timeout, cancelled):
        """Only the existing two public help endpoints, under the frozen owner."""
        snapshot, scene, configured_timeout, fmt = frozen
        proc, token, owner, scope, base = snapshot
        with self.lock:
            cfg = self.settings
            if (cancelled.is_set() or self.stopping.is_set() or self.proc is not proc
                    or self.token != token or not cfg or cfg.get('ownerBot') != owner
                    or cfg.get('scopeId') != scope or cfg.get('base') != base
                    or self.ctx.get('bot_id') != owner or proc.poll() is not None
                    or str(cfg.get('scene') or 'group') != scene
                    or float(cfg.get('commandTimeout') or 90) != configured_timeout
                    or str(cfg.get('mediaFormat') or 'png') != fmt):
                raise RuntimeError('public snapshot expired; no I/O started')
            if not ((path == '/api/commands' and payload is None)
                    or (path == '/api/chat' and isinstance(payload, dict)
                        and payload == {'command': 'ww帮助', 'scene': scene})):
                raise ValueError('public query refuses account or arbitrary endpoints')
            answer = self.request(path, payload, timeout, _cancelled=cancelled)
            self._assert_generation(snapshot, cancelled)
            return answer


    def prepare_command(self):
        """Freeze the existing explicit command API, not a QQ identity inference."""
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('command instance is busy; no I/O started')
        try:
            snapshot = self.prepare_status()
            if isinstance(snapshot, str):
                raise RuntimeError('command instance is offline; no I/O started')
            cfg = self.settings
            return (snapshot, str(cfg.get('scene') or 'group'),
                    float(cfg.get('commandTimeout') or 90),
                    str(cfg.get('mediaFormat') or 'html'))
        finally:
            self.lock.release()


    def receipt_request_for(self, frozen, identity, path, encoded, timeout, cancelled):
        """Frozen trusted QQ caller + service generation; no command retry/poll."""
        import hashlib
        snapshot, scene, configured_timeout, fmt = frozen
        proc, token, owner, scope, base = snapshot
        uid, audience, mid = identity
        with self.lock:
            def check():
                cfg = self.settings
                if (cancelled.is_set() or self.stopping.is_set() or self.proc is not proc
                        or self.token != token or not cfg or cfg.get('ownerBot') != owner
                        or cfg.get('scopeId') != scope or cfg.get('base') != base
                        or self.ctx.get('bot_id') != owner or proc.poll() is not None
                        or str(cfg.get('scene') or 'group') != scene
                        or float(cfg.get('commandTimeout') or 90) != configured_timeout
                        or str(cfg.get('mediaFormat') or 'html') != fmt):
                    raise RuntimeError('command snapshot expired; result unconfirmed')
            check()
            if (not isinstance(uid, str) or not uid.isdigit()
                    or not isinstance(audience, str) or not audience
                    or not isinstance(mid, str) or not mid):
                raise ValueError('missing frozen QQ caller')
            payload = json.loads(encoded)
            target_scene = 'group' if audience.startswith('group:') else 'direct'
            if path == '/api/health':
                valid = payload is None and timeout == 5
            elif path == '/api/chat':
                valid = (isinstance(payload, dict)
                    and set(payload) in ({'command', 'scene', 'user_id', 'audience',
                                          'bridge_receipts', 'request_id'},
                                         {'command', 'scene', 'user_id', 'audience',
                                          'bridge_receipts', 'request_id', 'images'})
                    and isinstance(payload.get('command'), str)
                    and bool(payload['command'].strip())
                    and payload.get('bridge_receipts') is True
                    and timeout == 12
                    and payload.get('request_id') == hashlib.sha256(json.dumps(
                        [audience, uid, mid, payload['command']], ensure_ascii=False).encode()).hexdigest()
                    and ('images' not in payload or (isinstance(payload['images'], list)
                        and all(isinstance(one, str) for one in payload['images']))))
            elif path == '/api/command-receipt':
                valid = (isinstance(payload, dict)
                    and set(payload) == {'job_id', 'cursor', 'user_id', 'scene', 'audience'}
                    and isinstance(payload.get('job_id'), str) and bool(payload['job_id'])
                    and type(payload.get('cursor')) is int and payload['cursor'] >= 0
                    and timeout == 8)
            else:
                valid = False
            if path != '/api/health' and valid:
                valid = (payload.get('user_id') == uid
                         and payload.get('audience') == audience
                         and payload.get('scene') == target_scene)
            if not valid:
                raise ValueError('receipt payload is not a frozen trusted request')
            answer = self.request(path, payload, timeout, _cancelled=cancelled)
            # Actual I/O completion does not prove it still belongs to this
            # service generation. Never turn an unknown late result into success.
            check()
            return answer

    def _assert_generation(self, snapshot, cancelled):
        """Caller holds lock; observed completion cannot authorize a new generation."""
        proc, token, owner, scope, base = snapshot
        cfg = self.settings
        if ((cancelled is not None and cancelled.is_set()) or self.stopping.is_set() or self.proc is not proc
                or self.token != token or not cfg or cfg.get('ownerBot') != owner
                or cfg.get('scopeId') != scope or cfg.get('base') != base
                or self.ctx.get('bot_id') != owner or proc.poll() is not None):
            raise RuntimeError('owned snapshot expired; result unconfirmed')
