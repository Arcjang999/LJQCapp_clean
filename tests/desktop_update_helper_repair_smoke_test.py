"""Pure temporary-file helper repair and crash recovery; no native app runs."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_updates.bootstrap import BootstrapError, install_bootstrap
from desktop_updates import helper_repair as repair


class Manager:
    def __init__(self, root, target):
        self.root, self.target = root, target
        self.locked, self.busy = False, False

    @contextmanager
    def maintenance(self):
        if self.busy or self.locked:
            raise BootstrapError("软件仍在使用，请先保存并退出。")
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def get_state(self):
        return json.loads((self.root / "updates/state.json").read_text())

    def register_base(self, *args, **kwargs):
        raise AssertionError("A helper repair must never re-register the application")


class HelperRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-helper-repair-")
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def helper(self, path, target, build, *, version="1.0.1"):
        path.mkdir(parents=True)
        resource = path / ("_internal" if target == "windows-x86_64" else "Contents/Resources")
        entry = path / ("LJQCAppUpdater.exe" if target == "windows-x86_64" else "Contents/MacOS/LJQCAppUpdater")
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(("helper-" + str(build)).encode())
        resource.mkdir(parents=True, exist_ok=True)
        info = {"version": version, "build_number": build, "data_format_version": 1,
                "source_dirty": False, "target": target,
                "git_commit": repair.OLD_HELPER_COMMIT if build == repair.OLD_HELPER_BUILD else "8" * 40,
                "source_digest": repair.OLD_HELPER_DIGEST if build == repair.OLD_HELPER_BUILD else "9" * 64}
        (resource / "build_info.json").write_text(json.dumps(info))
        trust = resource / "resources/desktop_update_trust.json"
        trust.parent.mkdir(parents=True)
        trust.write_bytes((ROOT / "resources/desktop_update_trust.json").read_bytes())
        if build >= repair.MIN_INVENTORIED_BUILD:
            (resource / "cacert.pem").write_bytes(b"new component CA resource fixture")
        if target == "macos-arm64":
            sealed = path / "Contents/_CodeSignature/CodeResources"
            sealed.parent.mkdir()
            sealed.write_bytes(("seal-" + str(build)).encode())
        return entry

    def fixture(self, target):
        area = self.root / target
        data = area / "customer-data"
        updates = data / "updates"
        updates.mkdir(parents=True)
        manager = Manager(data, target)
        installed = area / ("Programs/LJQCAppUpdater" if target == "windows-x86_64" else "Applications/LJQCApp.app")
        entry = self.helper(installed, target, repair.OLD_HELPER_BUILD)
        source = area / "download" / ("LJQCAppUpdater" if target == "windows-x86_64" else "LJQCAppUpdater.app")
        self.helper(source, target, repair.REPAIR_HELPER_BUILD)
        repair.write_helper_manifest(source, target)
        original_base = area / "retained-base-06-program"
        original_base.write_bytes(b"original 1.00 program, never replace")
        active_program = area / "already-updated-program"
        active_program.write_bytes(b"existing active version, never roll back")
        customer = data / "actual-customer.db"
        customer.write_bytes(b"opaque customer data; never opened")
        config = data / "config/storage_config.json"
        config.parent.mkdir()
        config.write_text('{"database_path":"/customer/chosen/location.db"}')
        state = {"format_version": 1, "base_release": repair.BASE_RELEASE, "target": target,
                 "updater_version": 1, "highest_sequence": 1794096988210362,
                 "highest_metadata_digest": "a" * 64, "updater_entrypoint": str(entry),
                 "current": {"path": str(area), "entrypoint": active_program.name,
                             "version": "1.0.1", "build_number": 2026110702, "target": target},
                 "base": {"path": str(area), "entrypoint": original_base.name,
                          "version": "1.0.0", "build_number": 2026110606, "target": target}}
        receipt = {"format": "ljqc-update-bootstrap", "format_version": 1,
                   "base_release": repair.BASE_RELEASE, "target": target, "updater_version": 1,
                   "updater_entrypoint": str(entry), "original_program": str(original_base),
                   "shortcuts": ["keep the original shortcut target"]}
        (updates / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        (updates / "bootstrap.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        retained = [updates / "state.json", updates / "bootstrap.json", original_base, active_program, customer, config]
        before = {path: path.read_bytes() for path in retained}
        return manager, source, installed, before

    def assert_retained(self, before):
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))

    def test_both_platforms_repair_02_helper_and_keep_all_application_state(self):
        for target in ("windows-x86_64", "macos-arm64"):
            with self.subTest(target=target):
                manager, source, installed, before = self.fixture(target)
                self.assertTrue(repair.needs_helper_repair(manager, source))
                checked = []
                repair.repair_registered_helper(manager, source, signature_checker=lambda path: checked.append(path))
                self.assert_retained(before)
                self.assertEqual(repair.read_helper_identity(installed, target)["build_number"], repair.REPAIR_HELPER_BUILD)
                manifest = repair.verify_helper_manifest(source, target)
                path = manager.root / "updates/helper-repair.json"
                journal = json.loads(path.read_text())
                self.assertEqual(journal["phase"], "complete")
                self.assertTrue(journal["state_unchanged"] and journal["bootstrap_receipt_unchanged"])
                self.assertEqual(repair._inventory(installed, target), manifest["files"])
                self.assertFalse(repair.needs_helper_repair(manager, source))
                if target == "macos-arm64":
                    self.assertEqual(len(checked), 4)

    def test_already_upgraded_current_version_and_replay_fence_are_not_reset(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        state = manager.get_state()
        state["current"].update(version="1.0.2", build_number=2026110901)
        path = manager.root / "updates/state.json"
        path.write_text(json.dumps(state))
        before[path] = path.read_bytes()
        repair.repair_registered_helper(manager, source)
        self.assert_retained(before)
        self.assertEqual(manager.get_state()["current"]["build_number"], 2026110901)
        self.assertEqual(manager.get_state()["highest_sequence"], 1794096988210362)

    def test_same_0801_component_is_idempotent_and_stable_component_never_overwrites_itself(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        repair.repair_registered_helper(manager, source)
        receipt = (manager.root / "updates/helper-repair.json").read_bytes()
        repair.repair_registered_helper(manager, source)
        repair.repair_registered_helper(manager, installed)
        self.assertEqual((manager.root / "updates/helper-repair.json").read_bytes(), receipt)
        self.assert_retained(before)

    def test_older_component_cannot_downgrade_repaired_helper(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        old = self.root / "old-download/LJQCAppUpdater"
        shutil.copytree(installed, old)
        repair.repair_registered_helper(manager, source)
        with self.assertRaises(BootstrapError):
            repair.repair_registered_helper(manager, old)
        self.assert_retained(before)

    def test_future_helper_factory_and_modern_to_newer_repair_preserve_state(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        repair.repair_registered_helper(manager, source)
        future = self.root / "future-download/LJQCAppUpdater"
        self.helper(future, manager.target, 2026110901, version="1.0.2")
        generated = repair.write_helper_manifest(future, manager.target)
        self.assertEqual(generated["release"]["version"], "1.0.2")
        self.assertEqual(repair.verify_helper_manifest(future, manager.target), generated)
        self.assertTrue(repair.needs_helper_repair(manager, future))
        repair.repair_registered_helper(manager, future)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], 2026110901)
        self.assert_retained(before)

    def test_future_first_adoption_still_requires_the_full_manifest(self):
        source = self.root / "future-download/LJQCAppUpdater.app"
        self.helper(source, "macos-arm64", 2026110901, version="1.0.2")
        base = self.root / "Applications/LJQCApp.app"
        base.mkdir(parents=True)
        original = base / "original-base.bin"
        original.write_bytes(b"keep the original base")
        manager = Manager(self.root / "empty-data-root", "macos-arm64")
        with self.assertRaises(repair.MissingHelperManifest):
            install_bootstrap(manager, base, source,
                validate=lambda *args: self.fail("Future components require an inventory before adoption"),
                signature_checker=lambda path: None)
        self.assertEqual(original.read_bytes(), b"keep the original base")
        self.assertFalse((manager.root / "updates/bootstrap.json").exists())

    def test_modern_identity_version_and_data_format_remain_strict(self):
        for number, invalid in enumerate(({"version": "1.2"}, {"version": "01.2.3"},
                {"version": ""}, {"version": "1.0.2-a..b"}, {"version": "1.0.2-01"},
                {"version": "1.0.2+.."}, {"data_format_version": True}, {"source_dirty": True})):
            with self.subTest(invalid=invalid):
                source = self.root / ("invalid-" + str(number)) / "LJQCAppUpdater"
                self.helper(source, "windows-x86_64", 2026110901, version="1.0.2")
                metadata = source / "_internal/build_info.json"
                value = json.loads(metadata.read_text())
                value.update(invalid)
                metadata.write_text(json.dumps(value))
                with self.assertRaises(BootstrapError):
                    repair.write_helper_manifest(source, "windows-x86_64")

    def test_busy_application_prevents_any_helper_change(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        old = repair._fingerprint(installed, manager.target)
        manager.busy = True
        with self.assertRaises(BootstrapError):
            repair.repair_registered_helper(manager, source)
        self.assertEqual(repair._fingerprint(installed, manager.target), old)
        self.assertFalse((manager.root / "updates/helper-repair.json").exists())
        self.assert_retained(before)

    def test_tampered_or_missing_new_source_manifest_fails_before_swap(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        (source / "_internal/cacert.pem").write_bytes(b"unexpected bytes")
        with self.assertRaises(BootstrapError):
            repair.repair_registered_helper(manager, source)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.OLD_HELPER_BUILD)
        self.assert_retained(before)

    def test_missing_sidecar_allows_explicit_source_selection_but_not_repair(self):
        manager, source, installed, before = self.fixture("macos-arm64")
        repair.helper_manifest_path(source, manager.target).unlink()
        self.assertTrue(repair.needs_helper_repair(manager, source))
        with self.assertRaises(repair.MissingHelperManifest):
            repair.repair_registered_helper(manager, source, signature_checker=lambda path: None)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.OLD_HELPER_BUILD)
        self.assert_retained(before)

    def test_first_adoption_of_0801_also_requires_complete_source_manifest(self):
        source = self.root / "download/LJQCAppUpdater.app"
        self.helper(source, "macos-arm64", repair.REPAIR_HELPER_BUILD)
        base = self.root / "Applications/LJQCApp.app"
        base.mkdir(parents=True)
        original = base / "original-base.bin"
        original.write_bytes(b"keep the original base")
        manager = Manager(self.root / "empty-data-root", "macos-arm64")
        with self.assertRaises(repair.MissingHelperManifest):
            install_bootstrap(manager, base, source,
                validate=lambda *args: self.fail("Missing source manifest must fail before base adoption"),
                signature_checker=lambda path: None)
        self.assertEqual(original.read_bytes(), b"keep the original base")
        self.assertFalse((manager.root / "updates/bootstrap.json").exists())
        self.assertFalse(list(manager.root.rglob("*.db")))

    def test_mac_signature_failure_cannot_replace_old_helper(self):
        manager, source, installed, before = self.fixture("macos-arm64")
        def reject(path):
            raise BootstrapError("模拟完整应用签名校验失败")
        with self.assertRaises(BootstrapError):
            repair.repair_registered_helper(manager, source, signature_checker=reject)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.OLD_HELPER_BUILD)
        self.assert_retained(before)

    def test_failure_after_old_helper_rename_restores_it(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        save = repair._save
        failed = False
        def fault(path, value):
            nonlocal failed
            if value.get("phase") == "old-retained" and not failed:
                failed = True
                raise OSError("simulated journal interruption")
            save(path, value)
        with patch.object(repair, "_save", side_effect=fault), self.assertRaises(OSError):
            repair.repair_registered_helper(manager, source)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.OLD_HELPER_BUILD)
        self.assertEqual(json.loads((manager.root / "updates/helper-repair.json").read_text())["phase"], "rolled-back")
        self.assert_retained(before)

    def test_stage_durability_failure_retains_the_old_helper(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        with patch.object(repair, "_sync_stage", side_effect=OSError("simulated flush failure")):
            with self.assertRaises(OSError):
                repair.repair_registered_helper(manager, source)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.OLD_HELPER_BUILD)
        self.assert_retained(before)

    def test_crash_recovery_restores_only_the_previous_helper(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        save = repair._save
        def fault(path, value):
            if value.get("phase") == "new-installed":
                raise OSError("simulated process death after switch")
            save(path, value)
        with patch.object(repair, "_save", side_effect=fault), \
                patch.object(repair, "_rollback", side_effect=OSError("simulated process already dead")):
            with self.assertRaises(OSError):
                repair.repair_registered_helper(manager, source)
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.REPAIR_HELPER_BUILD)
        result = repair.recover_helper_repair(manager)
        self.assertEqual(result["phase"], "rolled-back")
        self.assertEqual(repair.read_helper_identity(installed, manager.target)["build_number"], repair.OLD_HELPER_BUILD)
        self.assert_retained(before)

    def test_mutated_registration_blocks_recovery_without_resetting_it(self):
        manager, source, installed, before = self.fixture("windows-x86_64")
        save = repair._save
        def fault(path, value):
            if value.get("phase") == "old-retained":
                raise OSError("simulated interruption")
            save(path, value)
        with patch.object(repair, "_save", side_effect=fault), \
                patch.object(repair, "_rollback", side_effect=OSError("simulated process ended")):
            with self.assertRaises(OSError):
                repair.repair_registered_helper(manager, source)
        state = manager.root / "updates/state.json"
        state.write_bytes(state.read_bytes() + b"\n")
        with self.assertRaises(BootstrapError):
            repair.recover_helper_repair(manager)
        self.assertEqual(state.read_bytes(), before[state] + b"\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
