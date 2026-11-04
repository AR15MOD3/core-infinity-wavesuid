"""Portable offline contracts: synthetic files only, no Core/QQ/account service.

Run: python -B -m unittest discover -s tests -v
These tests do not certify a live Core host, upstream API, or QQ delivery.
"""
import asyncio
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load('test_waves_entry', 'index.py')
runtime = load('test_waves_runtime', 'scoped_runtime.py')
provision = load('test_waves_provision', 'tools/provision.py')
scope = load('test_waves_identity', 'service/scope_identity.py')
media = load('test_waves_media', 'service/rover/media_cache.py')
work = load('test_waves_work', 'service/rover/work_owner.py')
sys.path.insert(0, str(ROOT / 'service'))
from rover import command_jobs


class Receipts(unittest.TestCase):
    def test_all_public_python_parses(self):
        for path in ROOT.rglob('*.py'):
            if any(part in {'.venv', '__pycache__', 'data', 'logs', '.git', 'plugins'}
                   for part in path.relative_to(ROOT).parts):
                continue
            with self.subTest(file=str(path.relative_to(ROOT))):
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))

    def test_versions_agree(self):
        package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(package['version'], bridge.PLUGIN_VERSION)
        self.assertEqual(package['version'], runtime.BRIDGE_VERSION)

    def test_card_url_survives_long_digest(self):
        value = bridge._receipt({'ok': True, 'segments': [
            {'type': 'html', 'url': '/api/media/synthetic.html', 'digest': 'x' * 10000}]},
            'http://127.0.0.1:12345', 'png')
        self.assertIn('http://127.0.0.1:12345/api/media/synthetic.html/png', value)
        self.assertIn('已截断', value)

    def test_pending_has_ticket_and_does_not_claim_delivery(self):
        value = bridge._receipt({'ok': True, 'job_id': 'SYNTHETIC', 'cursor': 1,
            'pending': True, 'state': 'pending', 'segments': []}, '', 'png')
        self.assertIn('SYNTHETIC', value)
        self.assertIn('不要循环催查或重发命令', value)
        self.assertNotIn('发送成功', value)

    def test_empty_failure_has_visible_receipt(self):
        self.assertEqual(bridge._receipt({'ok': False, 'summary': 'SYNTHETIC rejected'}, '', 'png'),
                         'SYNTHETIC rejected')

    def test_login_kind_uses_configured_prefix(self):
        self.assertEqual(bridge._login_kind('ww登录', ['ww']), (True, False))
        self.assertEqual(bridge._login_kind('ww登录 SYNTHETIC', ['ww']), (True, True))
        self.assertEqual(bridge._login_kind('ww卡片', ['ww']), (False, False))

    def test_exception_receipt_redacts_raw_error(self):
        self.assertNotIn('SYNTHETIC_SECRET', bridge._why(RuntimeError('SYNTHETIC_SECRET')))


class Provisioning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='waves-synthetic-')
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / 'package'
        for file in ('scoped_main.py', 'scope_identity.py', 'rover/main.py', 'rover/data_store.py'):
            dest = self.package / 'service' / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text('# SYNTHETIC\n', encoding='utf-8')
        (self.package / 'config.example.json').write_text('{}', encoding='utf-8')
        self.service = self.root / 'service-v1'
        self.config = self.root / 'bots/ExampleBot/infinity/plugins/core-infinity-wavesuid/config.json'
        self.data = self.config.parent / 'runtime/data'

    def tearDown(self):
        self.temp.cleanup()

    def run_plan(self, **kwargs):
        return provision.provision(self.package, self.service, self.config, 'ExampleBot',
            self.data, 19778, self.service / '.venv/python', **kwargs)

    def test_plan_writes_nothing(self):
        result = self.run_plan()
        self.assertFalse(result['applied'])
        self.assertFalse(self.service.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.data.exists())

    def test_new_config_and_idempotent_install(self):
        self.run_plan(apply=True, write_config=True)
        before = self.config.read_bytes()
        self.run_plan(apply=True, write_config=True)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(json.loads(before)['ownerBot'], 'ExampleBot')
        self.assertFalse(self.data.exists(), 'installer must not create an account database')

    def test_existing_config_preserved_byte_for_byte(self):
        self.config.parent.mkdir(parents=True)
        before = b'{"ownerBot":"ExampleBot", "custom":"SYNTHETIC", "serviceDir":"old"}\n'
        self.config.write_bytes(before)
        self.run_plan(apply=True, write_config=True)
        self.assertEqual(self.config.read_bytes(), before)

    def test_foreign_owner_rejected(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"ownerBot":"OtherBot"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_plan(apply=True)
        self.assertFalse(self.service.exists())

    def test_existing_different_code_not_overwritten(self):
        self.service.mkdir()
        target = self.service / 'scoped_main.py'
        target.write_bytes(b'# SYNTHETIC OTHER VERSION')
        with self.assertRaises(ValueError):
            self.run_plan(apply=True)
        self.assertEqual(target.read_bytes(), b'# SYNTHETIC OTHER VERSION')

    def test_private_files_excluded(self):
        for name in ('data/private.db', 'logs/private.log', 'config.json', '__pycache__/x.pyc'):
            p = self.package / 'service' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'SYNTHETIC')
        self.assertEqual(len(list(provision.files(self.package))), 4)

    def test_exclusive_publish_preserves_concurrent_writer(self):
        target = self.root / 'concurrent.json'
        target.write_bytes(b'SYNTHETIC FIRST')
        with self.assertRaises(FileExistsError):
            provision.exclusive_atomic(target, b'SYNTHETIC SECOND')
        self.assertEqual(target.read_bytes(), b'SYNTHETIC FIRST')

    def test_data_outside_bot_resource_rejected(self):
        self.data = self.root / 'shared-data'
        with self.assertRaises(ValueError):
            self.run_plan(apply=True)
        self.assertFalse(self.service.exists())


class DataOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='waves-lease-synthetic-')
        self.root = Path(self.temp.name).resolve() / 'data'
        self.env = patch.dict(os.environ, {'ROVER_OWNER_BOT': 'ExampleBot',
            'ROVER_DATA_DIR': str(self.root), 'ROVER_INSTANCE_TOKEN': 'SYNTHETIC',
            'ROVER_SCOPE_ID': runtime.fingerprint('ExampleBot', self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_lease_reopens_only_for_same_owner(self):
        with scope.DataLease():
            self.assertTrue((self.root / '.waves-owner.json').exists())
        with scope.DataLease():
            pass
        with patch.dict(os.environ, {'ROVER_OWNER_BOT': 'OtherBot',
                'ROVER_SCOPE_ID': runtime.fingerprint('OtherBot', self.root)}):
            with self.assertRaises(ValueError):
                with scope.DataLease():
                    self.fail('foreign owner admitted')

    def test_unowned_nonempty_root_not_adopted(self):
        self.root.mkdir()
        (self.root / 'synthetic.db').write_bytes(b'SYNTHETIC')
        with self.assertRaises(ValueError):
            with scope.DataLease():
                self.fail('legacy account root adopted')

    def test_wrong_scope_rejected_before_creating_data(self):
        with patch.dict(os.environ, {'ROVER_SCOPE_ID': 'SYNTHETIC WRONG'}):
            with self.assertRaises(ValueError):
                with scope.DataLease():
                    self.fail('wrong scope admitted')
        self.assertFalse(self.root.exists())

    def test_fingerprints_are_bot_specific(self):
        self.assertNotEqual(runtime.fingerprint('ExampleBot', self.root),
                            runtime.fingerprint('OtherBot', self.root))


class MediaCache(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='waves-media-synthetic-')
        self.source = Path(self.temp.name) / 'synthetic.html'
        self.source.write_text('<html><p>SYNTHETIC</p></html>', encoding='utf-8')
        self.target = self.source.with_suffix('.png')
        self.target.write_bytes(b'SYNTHETIC PNG BYTES')

    def tearDown(self):
        self.temp.cleanup()

    def test_recency_does_not_mutate_source_or_signature(self):
        os.utime(self.source, (1, 1))
        before = self.source.read_bytes(), self.source.stat().st_mtime, media.signature(self.source)
        media.remember_access(self.source, interval=0)
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_mtime,
                          media.signature(self.source)), before)

    def test_cached_png_requires_content_and_metadata_match(self):
        signature = media.signature(self.source)
        self.assertIsNotNone(signature)
        self.assertFalse(media.valid_png(self.source, self.target, signature))
        media.remember_png(self.target, signature)
        self.assertTrue(media.valid_png(self.source, self.target, signature))
        self.target.write_bytes(b'SYNTHETIC CHANGED')
        self.assertFalse(media.valid_png(self.source, self.target, signature))

    def test_dynamic_resources_are_not_cacheable(self):
        for html in ('<img src="https://example.invalid/a">', '<script>Math.random()</script>',
                     '<style>p{background:url(https://example.invalid/a)}</style>'):
            self.source.write_text(html, encoding='utf-8')
            self.assertIsNone(media.signature(self.source))

    def test_stale_png_rejected(self):
        signature = media.signature(self.source)
        with patch.object(media.time, 'time', return_value=1000):
            media.remember_png(self.target, signature)
        with patch.object(media.time, 'time', return_value=2000):
            self.assertFalse(media.valid_png(self.source, self.target, signature))


class WorkOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_normal_work_drains(self):
        owner = work.WorkOwner()
        task = owner.spawn(lambda: asyncio.sleep(0))
        await task
        await asyncio.sleep(0)
        await owner.close()
        self.assertTrue(owner.state()['drained'])

    async def test_sealed_owner_rejects_without_invoking_factory(self):
        owner = work.WorkOwner()
        owner.seal()
        calls = []
        with self.assertRaises(RuntimeError):
            owner.spawn(lambda: calls.append(True))
        self.assertEqual(calls, [])

    async def test_pending_work_prevents_false_retirement(self):
        owner = work.WorkOwner()
        gate = asyncio.Event()
        task = owner.spawn(gate.wait)
        with self.assertRaises(RuntimeError):
            await owner.close(timeout=0)
        self.assertEqual(owner.state()['pending'], 1)
        gate.set()
        await task
        await asyncio.sleep(0)
        await owner.close()

    async def test_work_failure_is_retained(self):
        owner = work.WorkOwner()
        async def fail():
            raise ValueError('SYNTHETIC')
        with self.assertRaises(ValueError):
            await owner.spawn(fail)
        await asyncio.sleep(0)
        with self.assertRaises(RuntimeError):
            await owner.close()
        self.assertEqual(owner.state()['failed'], 1)


class CommandReceipts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.jobs = command_jobs.CommandJobs()
        self.work = work.WorkOwner()
        self.owner = ('SYNTHETIC_USER', 'group', 'group:qq:SYNTHETIC_GROUP')
        self.patch = patch.object(command_jobs, 'work_owner', self.work)
        self.patch.start()
        self.wait_patch = patch.object(command_jobs, 'FIRST_WAIT', 0.01)
        self.wait_patch.start()

    async def asyncTearDown(self):
        await self.jobs.close()
        await asyncio.sleep(0)
        self.wait_patch.stop()
        self.patch.stop()

    async def render(self, segments):
        return segments

    async def test_completed_receipt_replay_does_not_execute_again(self):
        calls = []
        async def execute(sink):
            calls.append(True)
            sink.append({'type': 'text', 'text': 'SYNTHETIC OK'})
            return {'matched': ['synthetic']}
        first = await self.jobs.submit(self.owner, 'SYNTHETIC_MESSAGE', execute, self.render)
        second = await self.jobs.submit(self.owner, 'SYNTHETIC_MESSAGE', execute, self.render)
        self.assertEqual(calls, [True])
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertFalse(second['new_command_started'])
        self.assertFalse(second['delivered_to_user'])

    async def test_foreign_user_cannot_read_receipt(self):
        job = command_jobs.Job(self.owner, 'SYNTHETIC_MESSAGE')
        job.sink.append({'type': 'text', 'text': 'SYNTHETIC PRIVATE'})
        self.jobs.jobs['SYNTHETIC_TICKET'] = job
        reply = await self.jobs.read('SYNTHETIC_TICKET', ('OTHER', *self.owner[1:]), 0, self.render)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['segments'], [])

    async def test_foreign_group_cannot_read_receipt(self):
        self.jobs.jobs['SYNTHETIC_TICKET'] = command_jobs.Job(self.owner, 'SYNTHETIC_MESSAGE')
        reply = await self.jobs.read('SYNTHETIC_TICKET', (*self.owner[:2], 'group:qq:OTHER'), 0, self.render)
        self.assertFalse(reply['ok'])

    async def test_invalid_cursor_rejected(self):
        self.jobs.jobs['SYNTHETIC_TICKET'] = command_jobs.Job(self.owner, 'SYNTHETIC_MESSAGE')
        for cursor in (-1, 1, True):
            with self.subTest(cursor=cursor):
                reply = await self.jobs.read('SYNTHETIC_TICKET', self.owner, cursor, self.render)
                self.assertFalse(reply['ok'])

    async def test_no_output_is_explicit_not_success(self):
        async def execute(sink):
            return {'matched': ['synthetic']}
        reply = await self.jobs.submit(self.owner, 'SYNTHETIC_MESSAGE', execute, self.render)
        self.assertEqual(reply['state'], 'no_receipt')
        self.assertFalse(reply['ok'])
        self.assertIn('结果未确认', reply['summary'])

    async def test_pending_ticket_blocks_second_command(self):
        gate, calls = asyncio.Event(), []
        async def execute(sink):
            calls.append(True)
            await gate.wait()
            sink.append({'type': 'text', 'text': 'SYNTHETIC OK'})
            return {}
        first = await self.jobs.submit(self.owner, 'SYNTHETIC_1', execute, self.render)
        second = await self.jobs.submit(self.owner, 'SYNTHETIC_2', execute, self.render)
        self.assertTrue(first['pending'])
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(calls, [True])
        gate.set()
        await self.jobs.jobs[first['job_id']].task


if __name__ == '__main__':
    unittest.main()
