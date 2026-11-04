"""Local trusted-process scope checks; not a security sandbox."""
import hashlib
import json
import os
from pathlib import Path


def install_middleware(app, token):
    """Keep authentication independently testable without upstream startup."""
    import hmac
    from fastapi.responses import JSONResponse
    if not isinstance(token, str) or not token:
        raise ValueError('nonempty instance token required')

    @app.middleware('http')
    async def require_instance(request, call_next):
        public = request.method == 'GET' and (request.url.path == '/api/health' or request.url.path.startswith('/api/media/'))
        browser = False
        if request.url.path.startswith('/waves/'):
            from rover.login_receipts import browser_allowed
            browser = await browser_allowed(request)
        if not public and not browser and not hmac.compare_digest(request.headers.get('x-waves-instance-token', '').encode('utf-8'), token.encode('utf-8')):
            return JSONResponse({'detail': 'instance mismatch'}, status_code=403)
        response = await call_next(request)
        if browser:
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.post('/api/owned-stop')
    async def owned_stop():
        from rover.work_owner import owner
        request_stop=getattr(app.state,'request_owned_stop',None)
        if not callable(request_stop):
            return JSONResponse({'accepted':False,'drained':False},status_code=503)
        owner.seal()
        request_stop()
        return {'accepted':True,'drained':False}


def identity():
    token = os.environ.get('ROVER_INSTANCE_TOKEN', '')
    from rover.work_owner import owner
    return {'bridge_version':'0.3.1','scope_id': os.environ.get('ROVER_SCOPE_ID', ''),
            'instance_id': hashlib.sha256(token.encode()).hexdigest() if token else '',
            'pid': os.getpid(),'work_observation':owner.state()}


class DataLease:
    def __init__(self):
        self.handle = None

    def __enter__(self):
        owner = os.environ.get('ROVER_OWNER_BOT', '')
        root = Path(os.environ.get('ROVER_DATA_DIR', ''))
        token = os.environ.get('ROVER_INSTANCE_TOKEN', '')
        expected = hashlib.sha256((owner + '\0' + os.path.normcase(str(root.resolve()))).encode()).hexdigest()
        if not owner or not root.is_absolute() or not token or expected != os.environ.get('ROVER_SCOPE_ID'):
            raise ValueError('missing or inconsistent service scope')
        root.mkdir(parents=True, exist_ok=True)
        self.handle = (root / '.waves.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0, 2)
                if self.handle.tell() == 0:
                    self.handle.write(b'0')
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            marker = root / '.waves-owner.json'
            value = {'ownerBot': owner, 'scopeId': expected}
            if marker.exists():
                if json.loads(marker.read_text(encoding='utf-8')) != value:
                    raise ValueError('data root belongs to another Bot')
            else:
                # Existing legacy/account data must be migrated explicitly,
                # never silently adopted merely because no marker exists.
                if any(p.name != '.waves.lock' for p in root.iterdir()):
                    raise ValueError('refusing unowned nonempty data root')
                with marker.open('x', encoding='utf-8') as stream:
                    json.dump(value, stream)
            return self
        except BaseException:
            self.handle.close()
            self.handle = None
            raise

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
            self.handle = None
