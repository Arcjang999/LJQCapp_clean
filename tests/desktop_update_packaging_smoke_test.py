"""Exercise patch export, release signatures and atomic static publication."""
from __future__ import annotations

import base64
import copy
from contextlib import nullcontext
from io import BytesIO
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from build_update_patch import export_patch
import publish_desktop_update as publication
from desktop_updates.payload import extract_verified_payload
from update_publish_policy import should_publish
from ci_verify_update_adoption import TemporaryKnownFolders, verify_adoption


class PatchPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-update-packing-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = Ed25519PrivateKey.generate()
        key_bytes = self.key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                          serialization.NoEncryption())
        self.key_file = self.root / "qa-only-private-key"
        self.key_file.write_bytes(key_bytes)
        self.key_file.chmod(0o600)
        self.trust = {"format_version": 1, "product": "LJQCApp", "channel": "dev",
            "base_release": "1.0.0-2026110606", "origin": "https://159.75.243.120",
            "keys": {"release-2026-11": base64.b64encode(self.key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")}}
        self.trust_file = self.root / "qa-trust.json"
        self.trust_file.write_text(json.dumps(self.trust))

    def metadata(self, target: str) -> dict:
        return {"version": "1.0.1", "build_number": 2026110701,
                "data_format_version": 1, "target": target, "git_commit": "c" * 40,
                "source_digest": "d" * 64, "source_dirty": False}

    def candidate(self, target: str, *, metadata: dict | None = None) -> tuple[Path, dict]:
        program = self.root / (target + "-program")
        program.mkdir()
        entry = program / ("LJQCApp.exe" if target == "windows-x86_64" else "LJQCApp.app/Contents/MacOS/LJQCApp")
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"a program fixture, never an actual user binary")
        entry.chmod(0o755)
        output = self.root / "inputs" / target
        # Windows chmod cannot express a POSIX executable bit. Only this tiny
        # synthetic Mac fixture is projected as 0755; native Mac builds use the
        # real filesystem mode and the production exporter remains unchanged.
        fixture_mode = (patch("build_update_patch.stat.S_IMODE", return_value=0o755)
                        if os.name == "nt" and target == "macos-arm64" else nullcontext())
        with fixture_mode:
            value = export_patch(program, metadata or self.metadata(target), output)
        helper = output / ("qa-bootstrap.exe" if target == "windows-x86_64" else "qa-bootstrap.dmg")
        helper.write_bytes(b"QA-only bootstrap fixture")
        (output / "bootstrap-build-receipt.json").write_text(json.dumps({"status": "passed", "target": target,
            "release": metadata or self.metadata(target), "artifact": {"file": helper.name,
            "sha256": publication.digest(helper), "size_bytes": helper.stat().st_size}}))
        return output, value

    def signed_pair(self) -> Path:
        for target in publication.TARGETS:
            self.candidate(target)
        output = self.root / "signed"
        publication.sign_candidates(self.root / "inputs", output, self.trust_file, key_path=self.key_file)
        return output

    def test_windows_patch_round_trip_normalizes_ntfs_modes(self):
        output, manifest = self.candidate("windows-x86_64")
        archive = output / manifest["artifact"]["url"].rsplit("/", 1)[-1]
        result = extract_verified_payload(archive, self.root / "extracted", manifest)
        self.assertFalse(result["mutable_data_included"])
        self.assertEqual(result["release"]["build_number"], 2026110701)
        self.assertEqual(next(row for row in result["files"] if row["path"] == "LJQCApp.exe")["mode"], 0o755)

    def test_mac_patch_round_trip_preserves_bundle_executable(self):
        output, manifest = self.candidate("macos-arm64")
        archive = output / manifest["artifact"]["url"].rsplit("/", 1)[-1]
        result = extract_verified_payload(archive, self.root / "extracted", manifest)
        self.assertEqual(result["files"][0]["mode"], 0o755)

    def test_dirty_source_cannot_create_publishable_patch(self):
        metadata = self.metadata("windows-x86_64")
        metadata["source_dirty"] = True
        with self.assertRaisesRegex(ValueError, "clean"):
            self.candidate("windows-x86_64", metadata=metadata)

    def test_patch_cannot_include_business_content(self):
        program = self.root / "bad-program"
        program.mkdir()
        (program / "LJQCApp.exe").write_bytes(b"fixture")
        (program / "qc_lj_app.db").write_bytes(b"SQLite format 3\x00")
        with self.assertRaisesRegex(ValueError, "Database"):
            export_patch(program, self.metadata("windows-x86_64"), self.root / "bad-output")

    def test_a_single_successful_platform_cannot_be_signed_as_a_release(self):
        self.candidate("windows-x86_64")
        with self.assertRaisesRegex(ValueError, "each platform"):
            publication.sign_candidates(self.root / "inputs", self.root / "signed", self.trust_file, key_path=self.key_file)

    def test_source_mismatch_blocks_pair_publication(self):
        self.candidate("windows-x86_64")
        metadata = self.metadata("macos-arm64")
        metadata["git_commit"] = "e" * 40
        self.candidate("macos-arm64", metadata=metadata)
        with self.assertRaisesRegex(ValueError, "same source"):
            publication.sign_candidates(self.root / "inputs", self.root / "signed", self.trust_file, key_path=self.key_file)

    def test_signing_key_must_match_shipped_trust(self):
        self.candidate("windows-x86_64")
        self.candidate("macos-arm64")
        self.trust["keys"]["release-2026-11"] = base64.b64encode(b"z" * 32).decode("ascii")
        self.trust_file.write_text(json.dumps(self.trust))
        with self.assertRaisesRegex(ValueError, "shipped trust"):
            publication.sign_candidates(self.root / "inputs", self.root / "signed", self.trust_file, key_path=self.key_file)

    def test_atomic_publication_preserves_base_and_rejects_tampering(self):
        bundle = self.signed_pair()
        index = publication.verify_bundle(bundle, self.trust_file)
        public = self.root / "public"
        base = public / "downloads/releases/1.0.0/2026110606/base-original.exe"
        base.parent.mkdir(parents=True)
        base.write_bytes(b"immutable original base")
        if sys.platform == "win32":
            self.assertEqual(index["base_release"], "1.0.0-2026110606")
        else:
            receipt = publication.promote(bundle, public, self.trust_file, public_verifier=lambda record: None)
            self.assertEqual(receipt["status"], "published")
            self.assertEqual(base.read_bytes(), b"immutable original base")
            for target in publication.TARGETS:
                self.assertEqual((public / "updates/desktop/dev" / target / "latest.json").read_bytes(),
                                 (bundle / target / "latest.json").read_bytes())
        (bundle / "windows-x86_64/latest.json").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash/length"):
            publication.verify_bundle(bundle, self.trust_file)

    def test_metadata_refresh_reuses_exact_programs_and_changes_no_base(self):
        bundle = self.signed_pair()
        old_index = publication.verify_bundle(bundle, self.trust_file)
        origin = self.trust["origin"]
        immutable = old_index["release_id"] + "-" + str(old_index["sequence"])
        urls = {origin + "/updates/desktop/dev/current/release-index.json": (bundle / "release-index.json").read_bytes()}
        for target in publication.TARGETS:
            urls[origin + f"/updates/desktop/dev/releases/{immutable}/{target}/latest.json"] = (bundle / target / "latest.json").read_bytes()
        refreshed = self.root / "refreshed"
        new_index = publication.refresh_metadata(refreshed, self.trust_file, key_path=self.key_file, fetch=urls.__getitem__)
        self.assertGreater(new_index["sequence"], old_index["sequence"])
        self.assertTrue(new_index["metadata_only"])
        self.assertEqual(len(list(refreshed.rglob("*.*"))), 3)
        publication.verify_bundle(refreshed, self.trust_file)
        for old, new in zip(old_index["files"], new_index["files"]):
            if old["kind"] != "manifest":
                self.assertEqual(old, new)
        if sys.platform != "win32":
            public = self.root / "public"
            publication.promote(bundle, public, self.trust_file, public_verifier=lambda record: None)
            publication.promote(refreshed, public, self.trust_file, public_verifier=lambda record: None)
            self.assertEqual(publication.read_object(public / "updates/desktop/dev/current/release-index.json")["signed"]["sequence"], new_index["sequence"])
            with self.assertRaisesRegex(ValueError, "older release"):
                publication.promote(bundle, public, self.trust_file, public_verifier=lambda record: None)

    def test_bootstrap_receipt_cannot_escape_candidate_directory(self):
        for target in publication.TARGETS:
            self.candidate(target)
        receipt = self.root / "inputs/windows-x86_64/bootstrap-build-receipt.json"
        value = publication.read_object(receipt)
        value["artifact"]["file"] = "../../qa-only-private-key"
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "plain candidate filename"):
            publication.sign_candidates(self.root / "inputs", self.root / "signed", self.trust_file, key_path=self.key_file)

    def test_https_failure_prevents_channel_selection(self):
        bundle = self.signed_pair()
        if sys.platform == "win32":
            publication.verify_bundle(bundle, self.trust_file)
            return  # The static receiver runs on the Unix cloud server.
        public = self.root / "public"
        def unavailable(record):
            raise ValueError("HTTPS publication URL did not verify")
        with self.assertRaisesRegex(ValueError, "HTTPS publication"):
            publication.promote(bundle, public, self.trust_file, public_verifier=unavailable)
        self.assertFalse((public / "updates/desktop/dev/current").is_symlink())

    def test_wrong_platform_symlink_cannot_silently_keep_an_old_release(self):
        bundle = self.signed_pair()
        if sys.platform == "win32":
            publication.verify_bundle(bundle, self.trust_file)
            return
        public = self.root / "public"
        fixed = public / "updates/desktop/dev/windows-x86_64"
        fixed.parent.mkdir(parents=True)
        fixed.symlink_to("old-wrong-release/windows-x86_64", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unexpected target"):
            publication.promote(bundle, public, self.trust_file, public_verifier=lambda record: None)
        self.assertFalse((fixed.parent / "current").is_symlink())

    def test_receiver_rejects_large_hidden_pax_header(self):
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            record = tarfile.TarInfo("release-index.json")
            record.size = 2
            record.pax_headers = {"comment": "x" * 65536}
            archive.addfile(record, BytesIO(b"{}"))
        stream.seek(0)
        with patch.object(publication.sys, "stdin", SimpleNamespace(buffer=stream)):
            with self.assertRaisesRegex(ValueError, "extended header"):
                publication.receive(self.root / "receiver-staging")
        self.assertFalse(list((self.root / "receiver-staging").iterdir()))

    def test_environment_publication_gate_respects_branch_event_and_manual_choice(self):
        cases = [
            ("refs/heads/main", "push", False, True, True),
            ("refs/heads/main", "push", False, False, False),
            ("refs/heads/main", "schedule", False, True, True),
            ("refs/heads/main", "schedule", False, False, False),
            ("refs/heads/main", "workflow_dispatch", False, True, False),
            ("refs/heads/main", "workflow_dispatch", True, False, True),
            ("refs/heads/feature", "push", True, True, False),
            ("refs/heads/feature", "workflow_dispatch", True, True, False),
        ]
        for ref, event, requested, automatic, expected in cases:
            with self.subTest(ref=ref, event=event, requested=requested, automatic=automatic):
                self.assertEqual(should_publish(ref=ref, event=event, requested=requested, automatic_enabled=automatic), expected)

    def test_minimal_metadata_refresh_environment_explicitly_installs_ca_dependency(self):
        recipe = (ROOT / ".github/workflows/desktop-update-publish.yml").read_text(encoding="utf-8")
        dependency = "python -m pip install cryptography==50.0.2 certifi==2026.7.22"
        self.assertIn(dependency, recipe)
        self.assertLess(recipe.index(dependency), recipe.index("publish_desktop_update.py refresh-metadata"))

    def test_real_installer_acceptance_refuses_local_execution_before_writing(self):
        destination = self.root / "must-not-be-created"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(ValueError, "ephemeral GitHub-hosted"):
                verify_adoption("windows-x86_64", self.root / "unused-inputs", destination)
        self.assertFalse(destination.exists())

    def test_temporary_known_folder_restore_retains_original_registry_values(self):
        fixture = object.__new__(TemporaryKnownFolders)
        fixture.qa = self.root
        original = {name: self.root / "original" / name for name in fixture.FOLDERS}
        paths = dict(original)
        registry = {key: {name: (str(path), 2) for name, path in original.items()} for key in fixture.KEYS}
        before = copy.deepcopy(registry)
        fixture.original, fixture.registry_before = {}, {}
        fixture.get = lambda name: paths[name]
        def set_path(name, path):
            paths[name] = path
            for key in registry:
                registry[key][name] = (str(path), 1)
        fixture.set = set_path
        fixture._values = lambda key: dict(registry[key])
        class RegistryKey:
            def __init__(self, path):
                self.path = path
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        fake_registry = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2,
            OpenKey=lambda root, path, reserved, access: RegistryKey(path),
            SetValueEx=lambda key, name, reserved, kind, value: registry[key.path].__setitem__(name, (value, kind)),
            DeleteValue=lambda key, name: registry[key.path].__delitem__(name))
        fixture.begin()
        self.assertTrue(all(path.is_relative_to(self.root / "KnownFolders") for path in paths.values()))
        with patch.dict(sys.modules, {"winreg": fake_registry}):
            fixture.restore()
        self.assertEqual(paths, original)
        self.assertEqual(registry, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
