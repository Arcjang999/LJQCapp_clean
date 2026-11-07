"""Signed bootstrap webpage links without database access or native Qt."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_updates.protocol import BASE_RELEASE, DEFAULT_ORIGIN, canonical_json
from services.desktop_update_catalog_service import get_desktop_update_catalog
from services.software_download_service import get_software_download_catalog


PAGE = "from ui.software_downloads import render_software_downloads_page; render_software_downloads_page()"


class DesktopUpdateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ljqc-bootstrap-catalog-")
        self.root = Path(self.temporary.name).resolve()
        self.index_path = self.root / "release-index.json"
        self.trust_path = self.root / "trust.json"
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.trust = {"format_version": 1, "product": "LJQCApp", "channel": "dev",
                      "base_release": BASE_RELEASE, "origin": DEFAULT_ORIGIN,
                      "keys": {"test-key": base64.b64encode(public).decode("ascii")}}
        self.trust_path.write_text(json.dumps(self.trust))
        self.now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
        self.base_bytes = (ROOT / "resources/software_downloads.json").read_bytes()

    def tearDown(self):
        self.assertEqual((ROOT / "resources/software_downloads.json").read_bytes(), self.base_bytes)
        self.temporary.cleanup()

    def release(self):
        targets, files = {}, []
        for number, target in enumerate(("windows-x86_64", "macos-arm64")):
            manifest_sha = ("a" if number == 0 else "b") * 64
            targets[target] = {"manifest_sha256": manifest_sha, "manifest_size_bytes": 512}
            filename = ("LJQCApp-UpdateBootstrap-1-2026110701-windows-x64-Setup.exe" if number == 0
                        else "LJQCApp-UpdateBootstrap-1-2026110701-macos-arm64-pending-signing.dmg")
            files.extend([
                {"target": target, "kind": "manifest", "path": target + "/latest.json",
                 "sha256": manifest_sha, "size_bytes": 512},
                {"target": target, "kind": "patch", "path": target + "/program.tar.gz",
                 "sha256": "c" * 64, "size_bytes": 2048},
                {"target": target, "kind": "bootstrap", "path": target + "/" + filename,
                 "url": DEFAULT_ORIGIN + "/downloads/update-bootstrap/1/2026110701/" + target + "/" + filename,
                 "sha256": "d" * 64, "size_bytes": 1024 * 1024},
            ])
        return {"format": "ljqc-desktop-release", "format_version": 1, "product": "LJQCApp",
                "channel": "dev", "base_release": BASE_RELEASE, "version": "1.0.1",
                "build_number": 2026110701, "sequence": 100, "source_commit": "e" * 40,
                "source_digest": "f" * 64, "source_dirty": False,
                "issued_at": self.now.isoformat(), "targets": targets, "files": files}

    def write(self, value):
        signed = {"signed": value, "key_id": "test-key",
                  "signature": base64.b64encode(self.key.sign(canonical_json(value))).decode("ascii")}
        self.index_path.write_bytes(canonical_json(signed))
        return signed

    def catalog(self, value=None):
        if value is not None:
            self.write(value)
        with patch.object(sqlite3, "connect", side_effect=AssertionError("Catalog opened SQLite")):
            return get_desktop_update_catalog(self.index_path, self.trust_path, now=self.now)

    def test_complete_signed_release_enables_both_helpers(self):
        catalog = self.catalog(self.release())
        self.assertTrue(catalog.readable)
        self.assertEqual([entry.target for entry in catalog.bootstraps], ["windows-x86_64", "macos-arm64"])
        self.assertTrue(all(entry.build_number == 2026110701 for entry in catalog.bootstraps))
        self.assertTrue(all(entry.url.startswith(DEFAULT_ORIGIN + "/downloads/update-bootstrap/1/")
                            for entry in catalog.bootstraps))

    def test_missing_unreadable_and_malformed_indices_are_unavailable(self):
        self.assertFalse(self.catalog().readable)
        for raw in (b"broken", b"[]", b"x" * 262145):
            with self.subTest(raw_size=len(raw)):
                self.index_path.write_bytes(raw)
                result = self.catalog()
                self.assertFalse(result.readable)
                self.assertFalse(result.bootstraps)

    def test_bad_signature_and_unknown_key_hide_all_helper_links(self):
        for key in ("signature", "key_id", "signed"):
            with self.subTest(key=key):
                raw = self.write(self.release())
                if key == "signed":
                    raw[key]["version"] = "9.9.9"
                else:
                    raw[key] = "unknown"
                self.index_path.write_bytes(canonical_json(raw))
                self.assertFalse(self.catalog().readable)

    def test_duplicate_json_keys_are_refused(self):
        raw = canonical_json(self.write(self.release()))
        raw = raw[:-1] + b',"key_id":"test-key"}'
        self.index_path.write_bytes(raw)
        self.assertFalse(self.catalog().readable)

    def test_wrong_release_identity_is_refused(self):
        invalid = {"base_release": "other-base", "channel": "stable", "source_dirty": True,
                   "source_commit": "not-a-commit", "source_digest": "invalid",
                   "format_version": True, "version": "", "build_number": True, "sequence": 0}
        for field, value in invalid.items():
            with self.subTest(field=field):
                release = self.release()
                release[field] = value
                self.assertFalse(self.catalog(release).readable)

    def test_both_platforms_and_manifest_hash_binding_are_required(self):
        for mutation in ("missing-target", "missing-file", "duplicate-kind", "wrong-manifest-hash", "bad-manifest-path"):
            with self.subTest(mutation=mutation):
                release = self.release()
                if mutation == "missing-target":
                    release["targets"].pop("macos-arm64")
                elif mutation == "missing-file":
                    release["files"].pop()
                elif mutation == "duplicate-kind":
                    release["files"][1]["kind"] = "bootstrap"
                elif mutation == "wrong-manifest-hash":
                    release["targets"]["windows-x86_64"]["manifest_sha256"] = "0" * 64
                else:
                    release["files"][0]["path"] = "windows-x86_64/not-latest.json"
                self.assertFalse(self.catalog(release).readable)

    def test_bootstrap_link_must_be_exact_https_platform_location(self):
        replacements = [
            (DEFAULT_ORIGIN, "http://159.75.243.120"),
            (DEFAULT_ORIGIN, "https://other.example.org"),
            ("/1/", "/2/"),
            ("/2026110701/", "/2026110702/"),
            ("/windows-x86_64/", "/macos-arm64/"),
            ("Setup.exe", "%53etup.exe"),
            ("Setup.exe", "Setup.exe?download=1"),
            ("Setup.exe", "Setup.exe#anchor"),
            ("/LJQCApp-", "/../LJQCApp-"),
        ]
        for before, after in replacements:
            with self.subTest(after=after):
                release = self.release()
                release["files"][2]["url"] = release["files"][2]["url"].replace(before, after)
                self.assertFalse(self.catalog(release).readable)

    def test_bootstrap_filename_hash_and_length_are_required(self):
        for field, value in (("path", "windows-x86_64/../helper.exe"), ("path", "windows-x86_64/helper.zip"),
                             ("sha256", "x" * 64), ("size_bytes", 0), ("size_bytes", True)):
            with self.subTest(field=field, value=value):
                release = self.release()
                release["files"][2][field] = value
                self.assertFalse(self.catalog(release).readable)

    def test_index_freshness_and_utc_are_checked(self):
        for issued in (self.now - timedelta(days=31, seconds=1), self.now + timedelta(minutes=5, seconds=1),
                       self.now.replace(tzinfo=None)):
            with self.subTest(issued=str(issued)):
                release = self.release()
                release["issued_at"] = issued.isoformat()
                self.assertFalse(self.catalog(release).readable)
        for issued in (self.now - timedelta(days=31), self.now + timedelta(minutes=5)):
            release = self.release()
            release["issued_at"] = issued.isoformat()
            self.assertTrue(self.catalog(release).readable)

    def test_metadata_refresh_keeps_original_bootstrap_link(self):
        release = self.release()
        release.update(sequence=200, metadata_only=True)
        self.assertTrue(self.catalog(release).readable)

    def test_environment_public_root_and_safe_current_symlink(self):
        self.write(self.release())
        public = self.root / "public"
        channel = public / "updates/desktop/dev"
        target = channel / "releases/fixture"
        target.mkdir(parents=True)
        (target / "release-index.json").write_bytes(self.index_path.read_bytes())
        (channel / "current").symlink_to("releases/fixture", target_is_directory=True)
        with patch.dict(os.environ, {"LJQC_PUBLIC_UPDATE_ROOT": str(public)}):
            self.assertTrue(get_desktop_update_catalog(trust_path=self.trust_path, now=self.now).readable)
        (channel / "current").unlink()
        (channel / "current").symlink_to(self.root, target_is_directory=True)
        with patch.dict(os.environ, {"LJQC_PUBLIC_UPDATE_ROOT": str(public)}):
            self.assertFalse(get_desktop_update_catalog(trust_path=self.trust_path, now=self.now).readable)

    def test_webpage_keeps_base_and_adds_signed_bootstrap_links(self):
        from streamlit.testing.v1 import AppTest
        helpers = self.catalog(self.release())
        with patch.dict(os.environ, {"LJQC_DEPLOYMENT_MODE": "cloud"}), \
                patch("ui.software_downloads.get_desktop_update_catalog", return_value=helpers), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("Download webpage opened SQLite")):
            app = AppTest.from_string(PAGE, default_timeout=15).run()
        self.assertFalse(list(app.exception), [error.message for error in app.exception])
        links = app.get("link_button")
        self.assertEqual(len(links), 4)
        expected = {entry.release.url for entry in get_software_download_catalog().downloads}
        expected.update(entry.url for entry in helpers.bootstraps)
        self.assertEqual({entry.proto.url for entry in links}, expected)
        text = "\n".join(entry.value for entry in app.markdown)
        self.assertIn("1.00 基础安装包固定保留", text)
        self.assertIn("无需手动拖动它替换原应用", text)
        self.assertIn("无需关闭系统安全保护", "\n".join(entry.value for entry in app.caption))

    def test_webpage_pending_helpers_leave_base_downloads_enabled(self):
        from streamlit.testing.v1 import AppTest
        helpers = self.catalog()
        with patch.dict(os.environ, {"LJQC_DEPLOYMENT_MODE": "cloud"}), \
                patch("ui.software_downloads.get_desktop_update_catalog", return_value=helpers):
            app = AppTest.from_string(PAGE, default_timeout=15).run()
        self.assertFalse(list(app.exception))
        self.assertEqual(len(app.get("link_button")), 2)
        self.assertEqual(len([item for item in app.button if item.label == "更新接入组件准备中" and item.disabled]), 2)

    def test_desktop_does_not_read_bootstrap_or_base_download_catalog(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"LJQC_DEPLOYMENT_MODE": "desktop"}), \
                patch("ui.software_downloads.get_desktop_update_catalog", side_effect=AssertionError("Desktop read update downloads")), \
                patch("ui.software_downloads.get_software_download_catalog", side_effect=AssertionError("Desktop read base downloads")):
            app = AppTest.from_string(PAGE, default_timeout=15).run()
        self.assertFalse(list(app.exception))
        self.assertEqual(len(app.get("link_button")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
