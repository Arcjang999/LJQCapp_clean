"""TLS diagnostic binding/CLI tests; no network, Qt or frozen executable runs."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_updates import launcher, tls_probe, transport
from desktop_updates.protocol import BASE_RELEASE, DEFAULT_ORIGIN, TARGETS, UpdateError, canonical_json


class TLSProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ljqc-source-tls-probe-qa-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.trust = {"format_version": 1, "product": "LJQCApp", "channel": "dev", "base_release": BASE_RELEASE,
                      "origin": DEFAULT_ORIGIN, "keys": {"qa": base64.b64encode(public).decode()}}
        now = datetime.now(timezone.utc)
        self.identity = {"version": "1.0.1", "build_number": 2026110801, "sequence": 100,
                         "source_commit": "e" * 40, "source_digest": "f" * 64}
        self.index = {**self.identity, "format": "ljqc-desktop-release", "format_version": 1,
                      "product": "LJQCApp", "channel": "dev", "base_release": BASE_RELEASE,
                      "source_dirty": False, "issued_at": now.isoformat(),
                      "release_id": "1.0.1-2026110801-" + "f" * 12, "targets": {}, "files": []}
        self.manifests = {}
        for target in TARGETS:
            windows = target == "windows-x86_64"
            manifest = {**self.identity, "format": "ljqc-desktop-update", "format_version": 1,
                "product": "LJQCApp", "edition": "desktop", "channel": "dev", "base_release": BASE_RELEASE,
                "target": target, "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=14)).isoformat(),
                "min_updater_version": 1, "data_format_version": 1, "min_os_version": "10.0.17763" if windows else "13.0",
                "artifact": {"url": DEFAULT_ORIGIN + "/downloads/patches/" + BASE_RELEASE + "/fixture.tar.gz",
                             "sha256": "a" * 64, "size_bytes": 123, "unpacked_size_bytes": 456, "kind": "tar.gz"},
                "payload": {"entrypoint": "LJQCApp.exe" if windows else "LJQCApp.app/Contents/MacOS/LJQCApp",
                            "manifest_sha256": "b" * 64}}
            self.manifests[target] = manifest
            name = "fixture.exe" if windows else "fixture.dmg"
            self.index["files"].extend([
                {"target": target, "kind": "patch", "path": target + "/program.tar.gz", "sha256": "c" * 64, "size_bytes": 456},
                {"target": target, "kind": "bootstrap", "path": target + "/" + name, "sha256": "d" * 64,
                 "size_bytes": 789, "url": DEFAULT_ORIGIN + "/downloads/update-bootstrap/1/2026110801/" + target + "/" + name}])

    def signed(self, value):
        return canonical_json({"signed": value, "key_id": "qa",
            "signature": base64.b64encode(self.key.sign(canonical_json(value))).decode()})

    def fixture_network(self):
        responses = {}
        self.index["files"] = [item for item in self.index["files"] if item["kind"] != "manifest"]
        prefix = DEFAULT_ORIGIN + "/updates/desktop/dev/releases/" + self.index["release_id"] + "-100/"
        for target, manifest in self.manifests.items():
            raw = self.signed(manifest)
            sha = hashlib.sha256(raw).hexdigest()
            self.index["targets"][target] = {"manifest_sha256": sha, "manifest_size_bytes": len(raw)}
            self.index["files"].append({"target": target, "kind": "manifest", "path": target + "/latest.json",
                                        "sha256": sha, "size_bytes": len(raw)})
            responses[prefix + target + "/latest.json"] = raw
        responses[DEFAULT_ORIGIN + "/updates/desktop/dev/current/release-index.json"] = self.signed(self.index)
        return responses

    def probe(self, responses=None):
        responses = self.fixture_network() if responses is None else responses
        with patch.object(transport, "fetch_metadata_url", side_effect=lambda trust, url: responses[url]) as network, \
                patch.object(tls_probe, "_reject_unknown_self_signed", return_value=True):
            result = tls_probe.run_tls_self_test(self.trust)
        return result, network

    def test_source_fixture_binds_index_and_both_immutable_platform_signatures(self):
        result, network = self.probe()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(network.call_count, 3)
        self.assertFalse(result["frozen"])
        self.assertFalse(result["business_storage_opened"])
        self.assertTrue(result["hostname_verification"])
        self.assertEqual(result["certificate_verification"], "CERT_REQUIRED")
        self.assertEqual(set(result["platform_manifests"]), TARGETS)

    def test_wrong_index_signature_is_rejected_before_manifest_network(self):
        responses = self.fixture_network()
        url = DEFAULT_ORIGIN + "/updates/desktop/dev/current/release-index.json"
        raw = json.loads(responses[url])
        raw["signed"]["version"] = "9.9.9"
        responses[url] = canonical_json(raw)
        with self.assertRaises(ValueError):
            self.probe(responses)

    def test_manifest_hash_signature_expiry_and_source_binding_are_mandatory(self):
        target = "macos-arm64"
        for failure in ("hash", "signature", "expired", "source"):
            with self.subTest(failure=failure):
                original = dict(self.manifests[target])
                if failure == "expired":
                    self.manifests[target]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
                elif failure == "source":
                    self.manifests[target]["source_commit"] = "0" * 40
                responses = self.fixture_network()
                url = next(url for url in responses if "/" + target + "/" in url)
                if failure in ("hash", "signature"):
                    value = json.loads(responses[url])
                    value["signed"]["version"] = "9.9.9"
                    responses[url] = canonical_json(value)
                    if failure == "signature":
                        sha = hashlib.sha256(responses[url]).hexdigest()
                        self.index["targets"][target] = {"manifest_sha256": sha, "manifest_size_bytes": len(responses[url])}
                        record = next(item for item in self.index["files"] if item["kind"] == "manifest" and item["target"] == target)
                        record.update(sha256=sha, size_bytes=len(responses[url]))
                        responses[DEFAULT_ORIGIN + "/updates/desktop/dev/current/release-index.json"] = self.signed(self.index)
                with self.assertRaises(UpdateError):
                    self.probe(responses)
                self.manifests[target] = original

    def test_mac_ca_data_may_be_in_same_bundle_resources_but_never_outside(self):
        contents = self.root / "LJQCAppUpdater.app/Contents"
        frameworks = contents / "Frameworks"
        frameworks.mkdir(parents=True)
        data = contents / "Resources/certifi/cacert.pem"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(frameworks), create=True):
            self.assertTrue(tls_probe._bundled_ca(data))
            self.assertFalse(tls_probe._bundled_ca(self.root / "outside/cacert.pem"))

    def test_tls_cli_precedes_storage_and_writes_explicit_no_gui_receipt(self):
        trust_file = self.root / "trust.json"
        trust_file.write_text(json.dumps(self.trust))
        result_file = self.root / "probe.json"
        with patch.object(launcher, "resource_path", return_value=trust_file), \
                patch.object(launcher, "default_data_root", side_effect=AssertionError("storage opened")), \
                patch.object(tls_probe, "run_tls_self_test", return_value={"status": "passed", "business_storage_opened": False}):
            self.assertEqual(launcher.main(["--tls-self-test", "--probe-result", str(result_file)]), 0)
        self.assertFalse(json.loads(result_file.read_text())["business_storage_opened"])

    def test_tls_cli_failure_returns_json_without_resolving_storage(self):
        trust_file = self.root / "trust.json"
        trust_file.write_text(json.dumps(self.trust))
        result_file = self.root / "failed.json"
        with patch.object(launcher, "resource_path", return_value=trust_file), \
                patch.object(launcher, "default_data_root", side_effect=AssertionError("storage opened")), \
                patch.object(tls_probe, "run_tls_self_test", side_effect=UpdateError("TLS rejected")):
            self.assertEqual(launcher.main(["--tls-self-test", "--probe-result", str(result_file)]), 1)
        self.assertEqual(json.loads(result_file.read_text())["status"], "failed")

    def test_ci_rejects_source_or_unverified_tls_receipt_from_executable(self):
        import certifi
        from ci_verify_update import verify_frozen_tls
        valid = {"status": "passed", "frozen": True, "bundled_ca": True, "trusted_ca_count": 121,
            "certifi_version": certifi.__version__, "ca_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
            "hostname_verification": True, "certificate_verification": "CERT_REQUIRED", "minimum_tls_version": "TLSv1_2",
            "unknown_self_signed_rejected": True, "release_index_signature_verified": True,
            "production_origin": DEFAULT_ORIGIN, "business_storage_opened": False,
            "platform_manifests": {target: {"signature_verified": True} for target in TARGETS}}
        for mutation in ({}, {"frozen": False}, {"bundled_ca": False}, {"hostname_verification": False},
                         {"certificate_verification": "CERT_NONE"}, {"unknown_self_signed_rejected": False},
                         {"minimum_tls_version": "TLSv1"}, {"release_index_signature_verified": False}):
            with self.subTest(mutation=mutation):
                result_file = self.root / "frozen-result.json"
                def simulate_frozen_call(*args, **kwargs):
                    result_file.write_text(json.dumps({**valid, **mutation}))
                with patch.dict(os.environ, GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted"), \
                        patch("ci_verify_update.subprocess.run", side_effect=simulate_frozen_call) as execution:
                    arguments = {"env": {}, "result_file": result_file, "log_file": self.root / "frozen.log"}
                    if mutation:
                        with self.assertRaises(ValueError):
                            verify_frozen_tls(self.root / "inert-helper", **arguments)
                    else:
                        self.assertEqual(verify_frozen_tls(self.root / "inert-helper", **arguments)["status"], "passed")
                    self.assertEqual(execution.call_args.kwargs["env"]["SSL_CERT_FILE"], str(self.root / "absent-ambient-ca.pem"))

    def test_ci_frozen_tls_probe_refuses_non_github_host_before_execution(self):
        from ci_verify_update import verify_frozen_tls
        with patch.dict(os.environ, GITHUB_ACTIONS="false"), patch("ci_verify_update.subprocess.run") as execution:
            with self.assertRaises(ValueError):
                verify_frozen_tls(self.root / "never-run", env={}, result_file=self.root / "result.json", log_file=self.root / "log")
            execution.assert_not_called()

    def test_registered_headless_install_never_requires_selecting_base_again(self):
        from desktop_updates import helper_repair, manager
        # The real integrated recovery entry must observe no pending journal,
        # then let this already registered installation follow helper repair.
        current = SimpleNamespace(directory=self.root / "data/updates",
            get_state=lambda: {"current": {"build_number": 2026110606}})
        with patch.object(manager, "UpdateManager", return_value=current), patch.object(launcher, "recover_bootstrap"), \
                patch.object(helper_repair, "recover_helper_repair"), \
                patch.object(helper_repair, "needs_helper_repair", return_value=False), \
                patch.object(helper_repair, "repair_registered_helper", return_value={"status": "already-current"}) as repair:
            self.assertEqual(launcher.main(["--install", "--no-gui", "--target", "windows-x86_64",
                "--helper", str(self.root / "source"), "--data-root", str(self.root / "data")]), 0)
        repair.assert_called_once_with(current, self.root / "source")
        self.assertFalse(current.directory.exists())

    def test_default_new_component_and_windows_install_choose_repair_ui_without_qt_import(self):
        from desktop_updates import helper_repair, manager
        current = SimpleNamespace(directory=self.root / "data/updates",
            get_state=lambda: {"current": {"build_number": 2026110606}})
        for flags, needed in (([], True), (["--install"], False)):
            with self.subTest(flags=flags):
                run_ui = Mock(return_value=0)
                with patch.object(manager, "UpdateManager", return_value=current), patch.object(launcher, "recover_bootstrap"), \
                        patch.object(helper_repair, "recover_helper_repair"), \
                        patch.object(helper_repair, "needs_helper_repair", return_value=needed), \
                        patch.dict(sys.modules, {"desktop_updates.qt_ui": SimpleNamespace(run_update_ui=run_ui)}):
                    self.assertEqual(launcher.main([*flags, "--target", "macos-arm64", "--helper", str(self.root / "source"),
                        "--data-root", str(self.root / "data")]), 0)
                self.assertTrue(run_ui.call_args.kwargs["repair_mode"])
                self.assertIsNone(run_ui.call_args.kwargs["base"])
                self.assertFalse(current.directory.exists())


if __name__ == "__main__":
    unittest.main()
