"""Isolated bootstrap swap/recovery tests; never touch real installed apps."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_updates import bootstrap


class FakeManager:
    def __init__(self, root, target):
        self.root, self.target, self.state = root, target, {}
        self.locked = False
        self.fail_registration = False

    @contextmanager
    def maintenance(self):
        assert not self.locked
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def get_state(self):
        return self.state

    def register_base(self, root, entrypoint, **kwargs):
        assert self.locked and kwargs["already_locked"]
        if self.fail_registration:
            raise bootstrap.BootstrapError("模拟接入失败")
        assert (root / entrypoint).is_file()
        self.state = {"current": {"path": str(root), "entrypoint": entrypoint},
                      "updater_entrypoint": kwargs.get("updater_entrypoint")}
        return self.state


def base_identity(target):
    return {"version": "1.0.0", "build_number": 2026110606,
            "source_digest": bootstrap.BASE_DIGEST, "git_commit": bootstrap.BASE_COMMIT,
            "target": target, "source_dirty": False}


def manifest_for(path, target):
    files = []
    for local in sorted(path.rglob("*")):
        if local.is_dir() and not local.is_symlink():
            continue
        relative = local.relative_to(path).as_posix()
        if target == "macos-arm64":
            relative = "LJQCApp.app/" + relative
        row = {"path": relative, "kind": "symlink" if local.is_symlink() else "file"}
        if local.is_symlink():
            row["target"] = local.readlink().as_posix()
        if local.is_file():
            row.update(size_bytes=local.stat().st_size, sha256=hashlib.sha256(local.read_bytes()).hexdigest())
        files.append(row)
    return {"release": base_identity(target), "files": files}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-bootstrap-test-")
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "资料 目录"
        self.data.mkdir()
        self.original_data = self.data / "private-business.db"
        self.original_data.write_bytes(b"never open or replace this file")
        self.config = self.data / "config/storage_config.json"
        self.config.parent.mkdir()
        self.config.write_text('{"database_path":"/chosen/by/customer.db"}')

    def tearDown(self):
        self.assertEqual(self.original_data.read_bytes(), b"never open or replace this file")
        self.assertEqual(self.config.read_text(), '{"database_path":"/chosen/by/customer.db"}')
        self.temporary.cleanup()

    def mac_apps(self):
        base = self.root / "Applications/LJQCApp.app"
        binary = base / "Contents/MacOS/LJQCApp"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"immutable original app")
        resource = base / "Contents/Resources"
        resource.mkdir()
        (resource / "real.txt").write_text("sealed resource")
        (resource / "alias.txt").symlink_to("real.txt")
        helper = self.root / "download/LJQCAppUpdater.app"
        entry = helper / "Contents/MacOS/LJQCAppUpdater"
        entry.parent.mkdir(parents=True)
        entry.write_bytes(b"standalone updater")
        return base, helper

    def windows_apps(self):
        base = self.root / "Programs/LJQCApp/versions/base"
        base.mkdir(parents=True)
        (base / "LJQCApp.exe").write_bytes(b"immutable original exe")
        helper = self.root / "download/updater"
        helper.mkdir(parents=True)
        (helper / "LJQCAppUpdater.exe").write_bytes(b"new updater")
        return base, helper

    def validator(self, path, target):
        return {"path": str(path.resolve()), **base_identity(target)}

    def test_pinned_baseline_receipts_are_available_for_both_targets(self):
        for target in bootstrap.BASE_MANIFEST_SHA256:
            manifest = bootstrap.read_baseline_manifest(target)
            self.assertEqual(manifest["release"], {**manifest["release"], **base_identity(target)})
            self.assertGreater(manifest["file_count"], 100)

    def test_baseline_verifies_files_and_internal_links(self):
        base, helper = self.mac_apps()
        manifest = manifest_for(base, "macos-arm64")
        identity = bootstrap.validate_baseline(base, "macos-arm64", manifest=manifest,
                                                verify_platform_signature=False)
        self.assertEqual(identity["source_digest"], bootstrap.BASE_DIGEST)
        (base / "Contents/Resources/real.txt").write_text("tampered")
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_baseline(base, "macos-arm64", manifest=manifest, verify_platform_signature=False)

    def test_baseline_rejects_extra_executable_and_escaping_link(self):
        base, helper = self.mac_apps()
        manifest = manifest_for(base, "macos-arm64")
        extra = base / "Contents/MacOS/extra"
        extra.write_text("not trusted")
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_baseline(base, "macos-arm64", manifest=manifest, verify_platform_signature=False)
        extra.unlink()
        alias = base / "Contents/Resources/alias.txt"
        alias.unlink()
        alias.symlink_to(self.original_data)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_baseline(base, "macos-arm64", manifest=manifest, verify_platform_signature=False)

    def test_wrong_build_identity_refused(self):
        base, _ = self.windows_apps()
        manifest = manifest_for(base, "windows-x86_64")
        manifest["release"]["build_number"] += 1
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_baseline(base, "windows-x86_64", manifest=manifest)

    def test_mac_swaps_original_entry_and_retains_exact_base(self):
        base, helper = self.mac_apps()
        manager = FakeManager(self.data, "macos-arm64")
        result = bootstrap.install_bootstrap(manager, base, helper, validate=self.validator,
                                               signature_checker=lambda path: None)
        self.assertEqual((base / "Contents/MacOS/LJQCAppUpdater").read_bytes(), b"standalone updater")
        current = manager.state["current"]
        original = Path(current["path"]) / current["entrypoint"]
        self.assertEqual(original.read_bytes(), b"immutable original app")
        self.assertTrue(original.parent.parent.joinpath("Resources/alias.txt").is_symlink())
        self.assertEqual(result["updater_entrypoint"], str(base / "Contents/MacOS/LJQCAppUpdater"))
        second = bootstrap.install_bootstrap(manager, base, helper, validate=lambda *args: self.fail("should be idempotent"))
        self.assertEqual(second, result)

    def test_mac_registration_failure_restores_original_entry(self):
        base, helper = self.mac_apps()
        manager = FakeManager(self.data, "macos-arm64")
        manager.fail_registration = True
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.install_bootstrap(manager, base, helper, validate=self.validator,
                                         signature_checker=lambda path: None)
        self.assertEqual((base / "Contents/MacOS/LJQCApp").read_bytes(), b"immutable original app")
        self.assertFalse((base / "Contents/MacOS/LJQCAppUpdater").exists())

    def test_mac_crash_between_rename_and_journal_update_restores(self):
        base, helper = self.mac_apps()
        manager = FakeManager(self.data, "macos-arm64")
        backup = base.with_name(".LJQCApp-base-2026110606-" + "a" * 32 + ".app")
        stage = base.with_name(".LJQCApp-update-stage-" + "b" * 32 + ".app")
        shutil.copytree(helper, stage)
        journal = {"format": "ljqc-bootstrap-journal", "format_version": 1,
                   "target": manager.target, "base": str(base), "installed": str(base),
                   "stage": str(stage), "backup": str(backup), "phase": "prepared"}
        bootstrap._atomic_json(self.data / "updates/bootstrap-journal.json", journal)
        base.rename(backup)
        result = bootstrap.recover_bootstrap(manager)
        self.assertEqual(result["phase"], "rolled-back")
        self.assertEqual((base / "Contents/MacOS/LJQCApp").read_bytes(), b"immutable original app")
        self.assertFalse(stage.exists())

    def test_mac_crash_after_state_commit_keeps_matching_entry(self):
        base, helper = self.mac_apps()
        manager = FakeManager(self.data, "macos-arm64")
        atomic = bootstrap._atomic_json
        def fail_once(path, value):
            if value.get("phase") == "registered":
                raise OSError("simulated disk fault")
            atomic(path, value)
        with patch.object(bootstrap, "_atomic_json", side_effect=fail_once):
            with self.assertRaises(OSError):
                bootstrap.install_bootstrap(manager, base, helper, validate=self.validator,
                                             signature_checker=lambda path: None)
        self.assertTrue((base / "Contents/MacOS/LJQCAppUpdater").is_file())
        result = bootstrap.recover_bootstrap(manager)
        self.assertEqual(result["phase"], "complete")
        current = manager.state["current"]
        self.assertTrue((Path(current["path"]) / current["entrypoint"]).is_file())

    def test_windows_registers_before_shortcut_switch(self):
        base, helper = self.windows_apps()
        manager = FakeManager(self.data, "windows-x86_64")
        calls = []
        def shortcuts(updater, old_entry, root):
            self.assertTrue(manager.state.get("current"))
            self.assertTrue(updater.is_file())
            calls.append((updater, old_entry, root))
            return {"shortcuts": ["isolated fixture"]}
        destination = self.root / "Programs/LJQCAppUpdater"
        result = bootstrap.install_bootstrap(manager, base, helper, validate=self.validator,
                            windows_destination=destination, shortcut_writer=shortcuts)
        self.assertEqual(len(calls), 1)
        self.assertEqual((base / "LJQCApp.exe").read_bytes(), b"immutable original exe")
        self.assertEqual(result["updater_entrypoint"], str(destination / "LJQCAppUpdater.exe"))

    def test_windows_shortcut_failure_can_resume_without_recopy(self):
        base, helper = self.windows_apps()
        manager = FakeManager(self.data, "windows-x86_64")
        destination = self.root / "Programs/LJQCAppUpdater"
        def failed(*args):
            raise bootstrap.BootstrapError("模拟入口写入失败")
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.install_bootstrap(manager, base, helper, validate=self.validator,
                                         windows_destination=destination, shortcut_writer=failed)
        self.assertTrue((destination / "LJQCAppUpdater.exe").exists())
        recovered = bootstrap.recover_bootstrap(manager, shortcut_writer=lambda *args: {"shortcuts": []})
        self.assertEqual(recovered["phase"], "complete")
        self.assertTrue((self.data / "updates/bootstrap.json").is_file())

    def test_recovery_rejects_arbitrary_journal_deletion_path(self):
        manager = FakeManager(self.data, "macos-arm64")
        journal = {"format": "ljqc-bootstrap-journal", "format_version": 1,
                   "target": manager.target, "base": str(self.root / "Applications/LJQCApp.app"),
                   "stage": str(self.data), "phase": "prepared"}
        bootstrap._atomic_json(self.data / "updates/bootstrap-journal.json", journal)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.recover_bootstrap(manager)
        self.assertTrue(self.original_data.is_file())

    def test_version_probe_does_not_create_explicit_data_root(self):
        data = self.root / "must-not-be-created"
        process = subprocess.run([sys.executable, str(ROOT / "desktop_updates/launcher.py"),
                                  "--version", "--data-root", str(data)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["base_release"], bootstrap.BASE_RELEASE)
        self.assertFalse(data.exists())


if __name__ == "__main__":
    unittest.main()
