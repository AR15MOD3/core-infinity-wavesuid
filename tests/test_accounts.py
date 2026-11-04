"""Account page backend tests: temporary synthetic SQLite, no real accounts."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('synthetic_waves_accounts', ROOT/'account_admin.py')
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class Accounts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='waves-page-synthetic-')
        self.scope = Path(self.temp.name).resolve()/'bots/ExampleBot/infinity/plugins/core-infinity-wavesuid'
        self.root = self.scope/'runtime/data'
        self.root.mkdir(parents=True)
        self.config = self.scope/'config.json'
        self.config.write_text(json.dumps(dict(ownerBot='ExampleBot', dataDir=str(self.root), port=19778)))
        self.path = self.root/'WavesData.db'
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript('''
CREATE TABLE WavesBind(id INTEGER PRIMARY KEY,user_id TEXT,bot_id TEXT,uid TEXT,pgr_uid TEXT);
CREATE TABLE WavesUser(id INTEGER PRIMARY KEY,user_id TEXT,bot_id TEXT,uid TEXT,game_id INTEGER,cookie TEXT,status TEXT);
CREATE TABLE WavesUserSdk(id INTEGER PRIMARY KEY,user_id TEXT,bot_id TEXT,uid TEXT);
CREATE TABLE WavesGachaCloud(id INTEGER PRIMARY KEY,user_id TEXT,bot_id TEXT,uid TEXT,login_info TEXT);
INSERT INTO WavesBind VALUES(1,'10001','dsh','123456789_123456780','9876543');
INSERT INTO WavesBind VALUES(2,'10002','dsh','123456789','');
INSERT INTO WavesUser VALUES(1,'10001','dsh','123456789',3,'SYNTHETIC_SECRET_1','');
INSERT INTO WavesUser VALUES(2,'10002','dsh','123456789',3,'SYNTHETIC_SECRET_2','expired');
INSERT INTO WavesUser VALUES(3,'10001','dsh','9876543',2,'SYNTHETIC_OTHER_GAME','');
INSERT INTO WavesUserSdk VALUES(1,'10001','dsh','123456789');
INSERT INTO WavesGachaCloud VALUES(1,'10001','dsh','123456789','SYNTHETIC_CLOUD_SECRET');
''')
        self.plugin = dict(name=admin.NAME, installed=True, enabled=False, loaded=False,
                           tracked=False, lifecycle='unloaded')
        def private_file(path):
            fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
            return os.fdopen(fd, 'wb')  # synthetic fixture, not production DACL assertion
        self.context = dict(bot_id='ExampleBot', config_path=str(self.config),
            data_dir=str(self.scope/'runtime'), plugin=self.plugin, open_private_new=private_file)
        self.api = admin.WavesAccounts(self.context)
        self.api.port_busy = lambda _: False

    def tearDown(self):
        self.temp.cleanup()

    def body(self):
        row = next(r for r in self.api.snapshot()['accounts'] if r['userId']=='10001' and r['uid']=='123456789')
        return dict(active='ExampleBot', id=row['id'], revision=row['revision'], confirm=True)

    def dump(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            return {t:list(c.execute('SELECT * FROM '+t+' ORDER BY id')) for t in admin.TABLES}

    def test_snapshot_redacts_credentials_and_keeps_separate_people(self):
        value = self.api.snapshot()
        self.assertEqual((value['people'], value['accountCount']), (2, 3))
        for word in ('SYNTHETIC_SECRET', 'cookie', 'login_info', 'pgr_uid'):
            self.assertNotIn(word, json.dumps(value))
        self.assertEqual(sum(r['loginSaved'] for r in value['accounts']), 2)

    def test_delete_preserves_other_user_uid_and_other_game(self):
        before = self.dump()
        self.api.delete(self.body())
        after = self.dump()
        self.assertEqual(after['WavesBind'][0][3:], ('123456780', '9876543'))
        self.assertEqual(after['WavesBind'][1], before['WavesBind'][1])
        self.assertEqual([r[0] for r in after['WavesUser']], [2, 3])
        self.assertEqual(after['WavesUserSdk'], [])
        self.assertEqual(after['WavesGachaCloud'], [])
        backup = list((self.scope/'account-backups').glob('*.json'))
        self.assertEqual(len(backup), 1)
        text = backup[0].read_text()
        self.assertIn('SYNTHETIC_SECRET_1', text)
        self.assertNotIn('SYNTHETIC_SECRET_2', text)

    def test_stale_revision_cannot_delete(self):
        body = self.body()
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("UPDATE WavesUser SET cookie='SYNTHETIC_CHANGED' WHERE id=1")
        before = self.dump()
        with self.assertRaises(ValueError): self.api.delete(body)
        self.assertEqual(self.dump(), before)

    def test_duplicate_delete_not_replayed(self):
        body = self.body()
        self.api.delete(body)
        before = self.dump()
        with self.assertRaises(ValueError): self.api.delete(body)
        self.assertEqual(self.dump(), before)

    def test_all_lifecycle_barriers_preserved(self):
        body = self.body()
        for key,value in [('enabled',True), ('loaded',True), ('tracked',True), ('lifecycle','stop_failed')]:
            with self.subTest(key=key):
                old = self.plugin[key]
                self.plugin[key] = value
                # Host freezes plugin state separately for each operator request.
                api = admin.WavesAccounts(self.context)
                api.port_busy = lambda _:False
                before = self.dump()
                with self.assertRaises(ValueError): api.delete(body)
                self.assertEqual(self.dump(), before)
                self.plugin[key] = old

    def test_listening_port_prevents_delete(self):
        body = self.body()
        self.api.port_busy = lambda _:True
        with self.assertRaises(ValueError): self.api.delete(body)

    def test_wrong_bot_body_rejected(self):
        body = self.body();body['active']='OtherBot'
        with self.assertRaises(ValueError): self.api.delete(body)

    def test_foreign_data_root_rejected(self):
        self.config.write_text(json.dumps(dict(ownerBot='ExampleBot',dataDir=str(self.scope.parent/'other'),port=19778)))
        with self.assertRaises(ValueError): self.api.snapshot()

    def test_foreign_config_owner_rejected(self):
        self.config.write_text(json.dumps(dict(ownerBot='OtherBot',dataDir=str(self.root),port=19778)))
        with self.assertRaises(ValueError): self.api.snapshot()

    def test_backup_failure_rolls_back(self):
        body = self.body();before = self.dump()
        def fail(_): raise OSError('SYNTHETIC backup failure')
        self.api.open_private_new = fail
        with self.assertRaises(OSError): self.api.delete(body)
        self.assertEqual(self.dump(), before)

    def test_transaction_failure_rolls_back_all_tables(self):
        body = self.body()
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON WavesUser BEGIN SELECT RAISE(ABORT,'SYNTHETIC'); END")
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError): self.api.delete(body)
        self.assertEqual(self.dump(), before)

    def test_missing_database_not_created_by_read(self):
        self.path.unlink()
        self.assertEqual(self.api.snapshot()['accounts'], [])
        self.assertFalse(self.path.exists())

    def test_unrecognized_action_rejected(self):
        with self.assertRaises(ValueError): admin.handle(self.context, 'start_service', {})

    def test_page_manifest_is_portable_and_bounded(self):
        manifest = json.loads((ROOT/'package.json').read_text())['telyincore']
        page = ROOT/manifest['configPage']
        self.assertLess(page.stat().st_size, 256000)
        text = page.read_text(encoding='utf-8')
        self.assertIn('event.source!==parent', text)
        self.assertNotIn('fetch(', text)
        self.assertNotIn('/infinity/waves', text)
        self.assertNotIn('启用插件', text)
        self.assertNotIn('停用插件</button>', text)
        self.assertEqual(manifest['pageBackend'], 'account_admin.py')


if __name__ == '__main__': unittest.main()
