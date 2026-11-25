import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_plugin_paths as paths
import native_registration as registration


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class RegistrationPath(unittest.TestCase):
    def test_only_direct_core_plugin_trees_have_owners(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "infinity/plugins/core-infinity-wavesuid/runtime/gscore"
            core = home / "gsuid_core"
            with patch.object(paths, "CORE_ROOT", core):
                for folder in ("plugins", "buildin_plugins"):
                    entry = core / folder / "Correct" / "vendor/plugins/Inner/task.py"
                    self.assertEqual(paths.plugin_parts(entry)[:2], (folder, "Correct"))
                for entry in (home / ".venv/plugins/dependency.py", core / "handler.py",
                              core / "utils/plugins/internal.py", home.parent / "another.py"):
                    self.assertEqual(paths.plugin_parts(entry), ())

    def test_first_fix_migration_idempotence_and_preflight(self):
        source = "def source_parts(path):\n    if path:\n        if path.name:\n" + registration._ORIGINAL
        fixed = registration._rewrite("sv.py", source)
        for newline in (b"\n", b"\r\n"):
            for first_fix in (False, True):
                with self.subTest(newline=newline, first_fix=first_fix), tempfile.TemporaryDirectory() as temp:
                    home = Path(temp)
                    path = home / "gsuid_core/sv.py"
                    path.parent.mkdir()
                    before = source.replace(registration._ORIGINAL, registration._FIRST_FIX) if first_fix else source
                    path.write_bytes(before.encode().replace(b"\n", newline))
                    with patch.dict(registration._PINS, {"sv.py": digest(source)}, clear=True), \
                         patch.dict(registration._PATCHED, {"sv.py": digest(fixed)}, clear=True):
                        registration.ensure_registration_root(home)
                        self.assertEqual(path.read_bytes(), fixed.encode().replace(b"\n", newline))
                        stamp = path.stat().st_mtime_ns
                        registration.ensure_registration_root(home)
                        self.assertEqual(stamp, path.stat().st_mtime_ns)
                        helper = path.with_name("_telyin_plugin_paths.py")
                        helper.unlink()
                        path.write_bytes(b"unexpected")
                        with self.assertRaises(RuntimeError):
                            registration.ensure_registration_root(home)
                        self.assertFalse(helper.exists())
                        self.assertEqual(path.read_bytes(), b"unexpected")
