"""Isolated installer payload, platform-path and graceful-shutdown regression."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


launcher = load("distribution_launcher_test", ROOT / "packaging/desktop_qt_launcher.py")
payload = load("distribution_payload_test", ROOT / "packaging/release_payload.py")


class DistributionTests(unittest.TestCase):
    def test_mac_outer_resources_find_native_service(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "有 空格.app/Contents/MacOS"
            service = base.parent / "Resources/app/LJQCAppService"
            service.parent.mkdir(parents=True)
            service.write_bytes(b"test service")
            with (patch.object(launcher, "_base_dir", return_value=base),
                  patch.object(launcher, "os", SimpleNamespace(name="posix"))):
                self.assertEqual(launcher._service_exe_path(), service)
                self.assertIn(base.parent / "Resources", launcher._resource_roots())

    def test_stable_root_is_forwarded_but_source_mode_stays_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(launcher, "get_storage_paths", return_value=SimpleNamespace(root=root, uses_stable_root=False)):
                    self.assertNotIn("LJQC_DATA_ROOT", launcher._service_environment())
                    self.assertEqual(launcher._service_environment()["LJQC_DEPLOYMENT_MODE"], "desktop")
                with patch.object(launcher, "get_storage_paths", return_value=SimpleNamespace(root=root, uses_stable_root=True)):
                    self.assertEqual(launcher._service_environment()["LJQC_DATA_ROOT"], str(root))
                    self.assertEqual(launcher._service_environment()["LJQC_DEPLOYMENT_MODE"], "desktop")

    def test_held_instance_lock_prevents_second_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher, "get_storage_paths", return_value=SimpleNamespace(root=Path(directory), uses_stable_root=True)):
                first, second = launcher._DesktopInstanceLock(), launcher._DesktopInstanceLock()
                try:
                    self.assertTrue(first.acquire())
                    self.assertFalse(second.acquire())
                    first.release()
                    self.assertTrue(second.acquire())
                finally:
                    first.release()
                    second.release()

    def test_shutdown_request_waits_and_removes_only_owned_request(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / ("shutdown-" + "a" * 32 + ".request")
            other = Path(directory) / "retained.txt"
            other.write_text("keep")
            process = SimpleNamespace(pid=8181, returncode=None)
            process.poll = lambda: process.returncode
            def wait(timeout=None):
                self.assertEqual(request.read_text(), "stop\n")
                self.assertEqual(timeout, 0.2)
                process.returncode = 0
            process.wait = wait
            launcher._SERVICE_SHUTDOWN_FILES[process.pid] = request
            self.assertTrue(launcher._stop_process_tree(process, timeout_seconds=0.2))
            self.assertFalse(request.exists())
            self.assertEqual(other.read_text(), "keep")

    def test_shutdown_timeout_keeps_request_and_does_not_force_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / ("shutdown-" + "b" * 32 + ".request")
            process = SimpleNamespace(pid=8282, poll=lambda: None)
            def wait(timeout=None):
                raise subprocess.TimeoutExpired("synthetic service", timeout)
            process.wait = wait
            launcher._SERVICE_SHUTDOWN_FILES[process.pid] = request
            try:
                with patch.object(launcher, "_write_log"):
                    self.assertFalse(launcher._stop_process_tree(process, timeout_seconds=0))
                self.assertTrue(request.exists())
                self.assertIn(process.pid, launcher._SERVICE_SHUTDOWN_FILES)
            finally:
                launcher._SERVICE_SHUTDOWN_FILES.pop(process.pid, None)

    def test_start_service_uses_private_unique_shutdown_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = root / "app/LJQCAppService"
            service.parent.mkdir()
            service.write_text("placeholder")
            captured = {}
            def popen(command, **kwargs):
                captured.update(command=command, **kwargs)
                return SimpleNamespace(pid=8383)
            with patch.object(launcher, "get_storage_paths", return_value=SimpleNamespace(root=root, uses_stable_root=True)), \
                 patch.object(launcher, "_service_exe_path", return_value=service), \
                 patch.object(launcher.subprocess, "Popen", side_effect=popen):
                process = launcher._start_service(18383)
            request = Path(captured["env"]["LJQC_SHUTDOWN_FILE"])
            self.assertRegex(request.name, r"^shutdown-[0-9a-f]{32}\.request$")
            self.assertFalse(request.exists())
            self.assertEqual(request.parent, root / "runtime")
            self.assertEqual(captured["env"]["LJQC_DATA_ROOT"], str(root))
            self.assertTrue(captured["stdout"].closed)
            launcher._SERVICE_SHUTDOWN_FILES.pop(process.pid)

    def test_payload_rejects_databases_even_with_another_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "normal.bin").write_bytes(b"program")
            (root / "looks-safe.bin").write_bytes(b"SQLite format 3\x00" + b"x" * 32)
            with self.assertRaisesRegex(ValueError, "SQLite content"):
                payload.validate_payload(root)

    def test_payload_rejects_saved_config_and_trial_tools(self):
        for name in ("storage_config.json", "写入演示数据.bat", "清除数据库.bat", "trial-data.zip"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / name).write_text("must not ship")
                with self.assertRaises(ValueError):
                    payload.validate_payload(root)

    def test_payload_allows_internal_framework_symlinks_but_not_external(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "native").write_bytes(b"native code")
            (root / "link").symlink_to("native")
            records = payload.validate_payload(root)
            self.assertEqual(next(r for r in records if r["path"] == "link")["kind"], "symlink")
            (root / "escape").symlink_to(Path(outside))
            with self.assertRaisesRegex(ValueError, "escapes"):
                payload.validate_payload(root)

    def test_new_release_assets_have_provenance_without_destructive_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "program"
            stage.mkdir()
            metadata = {"version": "1.0.0", "build_number": 2026110601,
                        "git_commit": "c" * 40, "source_dirty": True,
                        "source_digest": "d" * 64, "target": "windows-x86_64"}
            metadata_path = root / "metadata.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "packaging/write_release_assets.py"),
                            "--staging", str(stage), "--metadata", str(metadata_path)], check=True)
            for base in (stage, stage / "_internal", stage / "_internal/app", stage / "_internal/app/_internal"):
                recorded = json.loads((base / "build_info.json").read_text())
                self.assertEqual(recorded["source_digest"], "d" * 64)
                self.assertEqual(recorded["git_commit"], "c" * 40)
            self.assertFalse((stage / "清除数据库.bat").exists())
            self.assertFalse((stage / "写入演示数据.bat").exists())
            manifest = json.loads((stage / payload.MANIFEST_NAME).read_text())
            self.assertFalse(manifest["mutable_data_included"])
            self.assertGreater(manifest["file_count"], 5)

    def test_installer_keeps_program_versions_separate_from_user_data(self):
        recipe = (ROOT / "packaging/windows_installer.iss").read_text(encoding="utf-8")
        self.assertIn("PrivilegesRequired=lowest", recipe)
        self.assertIn(r'DestDir: "{app}\versions\{#PayloadId}"', recipe)
        self.assertNotIn("[UninstallDelete]", recipe)
        self.assertIn("AppMutex=Local\\LJQCApp.Desktop", recipe)
        self.assertIn("MinVersion=10.0.17763", recipe)


if __name__ == "__main__":
    unittest.main(verbosity=2)
