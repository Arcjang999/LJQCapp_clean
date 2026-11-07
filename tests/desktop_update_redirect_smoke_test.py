"""Frozen desktop entry redirection, without launching Qt or real processes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("desktop_update_redirect_launcher", ROOT / "packaging/desktop_qt_launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-entry-redirect-")
        self.root = Path(self.temporary.name).resolve()
        self.state_path = self.root / "updates/state.json"
        self.state_path.parent.mkdir()
        self.current = self.root / "program/LJQCApp.app/Contents/MacOS/LJQCApp"
        self.current.parent.mkdir(parents=True)
        self.current.write_bytes(b"registered program")
        self.helper = self.root / "Applications/LJQCApp.app/Contents/MacOS/LJQCAppUpdater"
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b"stable updater")
        self.state = {"format_version": 1, "base_release": "1.0.0-2026110606", "target": "macos-arm64",
            "current": {"path": str(self.root / "program"),
                        "entrypoint": "LJQCApp.app/Contents/MacOS/LJQCApp", "target": "macos-arm64"},
            "updater_entrypoint": str(self.helper)}
        self.storage = SimpleNamespace(root=self.root, uses_stable_root=True)

    def tearDown(self):
        self.temporary.cleanup()

    def write_state(self):
        self.state_path.write_text(json.dumps(self.state))

    def test_source_maintenance_managed_and_unregistered_entries_do_not_redirect(self):
        self.write_state()
        with patch.object(launcher, "get_storage_paths", return_value=self.storage), \
                patch.object(launcher, "_spawn_stable_update_helper", side_effect=AssertionError("unexpected process")):
            with patch.object(launcher.sys, "frozen", False, create=True):
                self.assertFalse(launcher._redirect_to_stable_update_helper([]))
            with patch.object(launcher.sys, "frozen", True, create=True):
                for flag in ("--version", "--diagnose", "--reset-db", "--update-preflight"):
                    self.assertFalse(launcher._redirect_to_stable_update_helper([flag]))
                with patch.dict(os.environ, {"LJQC_LAUNCHED_BY_UPDATER": "1"}):
                    self.assertFalse(launcher._redirect_to_stable_update_helper([]))
                self.state_path.unlink()
                self.assertFalse(launcher._redirect_to_stable_update_helper(["--auto-close-seconds", "1"]))

    def test_valid_state_redirects_before_desktop_lock_and_resets_child_environment(self):
        self.write_state()
        with patch.object(launcher.sys, "frozen", True, create=True), \
                patch.object(launcher.sys, "platform", "darwin"), \
                patch.dict(os.environ, {"QT_PLUGIN_PATH": "/old/qt", "LJQC_LAUNCHED_BY_UPDATER": "0"}), \
                patch.object(launcher, "get_storage_paths", return_value=self.storage), \
                patch.object(launcher, "_spawn_stable_update_helper") as spawn, \
                patch.object(launcher, "_DesktopInstanceLock", side_effect=AssertionError("Redirect acquired lock")):
            self.assertEqual(launcher.main([]), 0)
        command, env = spawn.call_args.args
        self.assertEqual(command, [str(self.helper), "--data-root", str(self.root)])
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(env["LJQC_DEPLOYMENT_MODE"], "desktop")
        self.assertNotIn("QT_PLUGIN_PATH", env)
        self.assertNotIn("LJQC_LAUNCHED_BY_UPDATER", env)

    def test_bad_registration_stops_before_program_or_database_can_start(self):
        mutations = [lambda: self.state.update(base_release="other"),
                     lambda: self.state.update(target="windows-x86_64"),
                     lambda: self.state.update(updater_entrypoint="relative/helper"),
                     lambda: self.state["current"].update(entrypoint="../escape"),
                     lambda: self.state.update(updater_entrypoint=str(self.root / "missing/LJQCAppUpdater"))]
        original = json.loads(json.dumps(self.state))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.state = json.loads(json.dumps(original))
                mutation()
                self.write_state()
                with patch.object(launcher.sys, "frozen", True, create=True), \
                        patch.object(launcher.sys, "platform", "darwin"), \
                        patch.dict(os.environ, {"LJQC_LAUNCHED_BY_UPDATER": "0"}), \
                        patch.object(launcher, "get_storage_paths", return_value=self.storage), \
                        patch.object(launcher, "_spawn_stable_update_helper", side_effect=AssertionError("Bad state spawned")), \
                        patch.object(launcher, "_DesktopInstanceLock", side_effect=AssertionError("Bad state acquired lock")), \
                        patch.object(launcher, "_message_box") as message:
                    self.assertEqual(launcher.main([]), 1)
                    self.assertIn("保留原资料", message.call_args.args[0])
        self.assertFalse(list(self.root.rglob("*.db")))

    def test_windows_dll_search_path_is_restored_even_when_child_spawn_fails(self):
        kernel = SimpleNamespace(GetDllDirectoryW=MagicMock(), SetDllDirectoryW=MagicMock(return_value=1))
        def previous(size, buffer):
            buffer.value = "C:\\old-qt"
            return len(buffer.value)
        kernel.GetDllDirectoryW.side_effect = previous
        command = [str(self.helper)]
        for failure in (None, OSError("synthetic spawn failure")):
            with self.subTest(failure=failure):
                kernel.SetDllDirectoryW.reset_mock()
                with patch.object(launcher, "os", SimpleNamespace(name="nt")), \
                        patch.object(launcher.ctypes, "WinDLL", return_value=kernel, create=True), \
                        patch.object(launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                        patch.object(launcher.subprocess, "Popen", side_effect=failure):
                    if failure:
                        with self.assertRaises(OSError):
                            launcher._spawn_stable_update_helper(command, {})
                    else:
                        launcher._spawn_stable_update_helper(command, {})
                self.assertEqual(kernel.SetDllDirectoryW.call_args_list[0].args, (None,))
                self.assertEqual(kernel.SetDllDirectoryW.call_args_list[-1].args, ("C:\\old-qt",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
