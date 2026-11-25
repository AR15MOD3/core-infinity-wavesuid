import asyncio
import json
from pathlib import Path
import runpy
import socket
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
setup = runpy.run_path(str(ROOT / 'tools/setup_environment.py'))
handle = runpy.run_path(str(ROOT / 'account_admin.py'))['handle']


class Setup(unittest.TestCase):
    def test_empty_bot_read_only_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / 'bots/Test/infinity/plugins/core-infinity-wavesuid'
            config = scope / 'config.json'
            ctx = dict(bot_id='Test', config_path=str(config), data_dir=str(scope/'runtime'), plugin={})
            result = handle(ctx, 'environment', {})
            self.assertFalse(result['ready'])
            self.assertNotEqual(result['nativePort'], result['loginPort'])
            self.assertFalse(scope.exists())
            with self.assertRaises(ValueError):
                handle(dict(ctx, bot_id='Other'), 'environment', {})
            with self.assertRaises(ValueError):
                handle(ctx, 'install_native', {'url':'untrusted'})
            scope.mkdir(parents=True)
            config.write_text(json.dumps({'ownerBot':'Other'}))
            with self.assertRaises(ValueError):
                handle(ctx, 'install_native', {})

    def test_retry_progress_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / 'bots/Test/infinity/plugins/core-infinity-wavesuid'
            folder = scope / 'runtime/setup'
            folder.mkdir(parents=True)
            ctx = dict(bot_id='Test', config_path=str(scope/'config.json'), data_dir=str(scope/'runtime'), plugin={})
            setup['write_state'](folder, 'running', 'test')
            with (folder/'install.lock').open('a+b') as lock:
                setup['acquire'](lock)
                self.assertTrue(handle(ctx, 'environment', {})['running'])
                self.assertIn('正在', handle(ctx, 'install_native', {})['message'])
            result = handle(ctx, 'environment', {})
            self.assertFalse(result['running'])
            self.assertEqual(result['progress']['phase'], 'interrupted')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                used = listener.getsockname()[1]
                self.assertNotEqual(setup['available_port'](used, 0), used)

    def test_hosted_readiness_uses_callback(self):
        try:
            import starlette
        except ImportError:
            self.skipTest('requires native runtime starlette')
        middleware = runpy.run_path(str(ROOT / 'native_service.py'))['InstanceSocket']
        async def check(code):
            messages = []
            async def send(message):
                messages.append(message)
            app = middleware(None, b'token', b'proof', lambda: code)
            scope = dict(type='http', method='GET', path='/_telyin/waves/group-login',
                         query_string=b'hosted', headers=[(b'x-waves-token', b'token')])
            await app(scope, None, send)
            self.assertEqual(messages[0]['status'], code)
        for status in (204, 409, 503):
            asyncio.run(check(status))
