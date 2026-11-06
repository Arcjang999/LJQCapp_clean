"""Published desktop links are complete, platform-specific and safe to display."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services.software_download_service import get_software_download_catalog


PAGE = "from ui.software_downloads import render_software_downloads_page; render_software_downloads_page()"


def published(**overrides):
    record = dict(published=True, version="1.2.3", published_at="2026-11-06",
                  url="https://downloads.example.org/releases/1.2.3/windows-x64.exe",
                  sha256="a" * 64, size_bytes=25_000_000)
    record.update(overrides)
    return record


class SoftwareDownloadsTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "software_downloads.json"

    def tearDown(self):
        self.temp.cleanup()

    def catalog(self, records):
        self.path.write_text(json.dumps(dict(format_version=1, releases=records)), encoding="utf-8")
        return get_software_download_catalog(self.path)

    def app(self, catalog):
        with patch("ui.software_downloads.get_software_download_catalog", return_value=catalog), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("Download page opened SQLite")):
            app = AppTest.from_string(PAGE, default_timeout=15).run()
        self.assertFalse(list(app.exception), [item.message for item in app.exception])
        return app

    def test_shipping_catalog_has_no_placeholder_links_or_release_versions(self):
        catalog = get_software_download_catalog()
        self.assertTrue(catalog.readable)
        self.assertEqual([item.target for item in catalog.downloads], ["windows-x86_64", "macos-arm64"])
        self.assertTrue(all(item.release is None for item in catalog.downloads))
        app = self.app(catalog)
        self.assertEqual(len(app.get("link_button")), 0)
        self.assertEqual(len([item for item in app.button if item.label == "安装包准备中" and item.disabled]), 2)
        self.assertFalse(app.button(key="software_downloads_home").disabled)
        self.assertFalse(any("版本 " in item.value for item in app.caption))

    def test_complete_published_release_is_the_only_displayed_link(self):
        record = published()
        catalog = self.catalog({"windows-x86_64": record, "macos-arm64": None,
                                "linux-x86_64": record})
        self.assertEqual(len(catalog.downloads), 2)
        self.assertEqual(catalog.downloads[0].release.published_at, date(2026, 11, 6))
        app = self.app(catalog)
        links = app.get("link_button")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].proto.url, record["url"])
        self.assertEqual(links[0].proto.label, "下载 Windows 安装包")
        self.assertTrue(any("1.2.3" in item.value and "2026-11-06" in item.value
                            and "25.0 MB" in item.value for item in app.caption))

    def test_invalid_record_does_not_hide_the_other_valid_platform(self):
        invalid = [
            {"url": "http://downloads.example.org/app.exe"},
            {"url": "https://user:secret@downloads.example.org/app.exe"},
            {"url": "javascript:alert(1)"},
            {"url": "file:///tmp/app.exe"},
            {"url": "https://downloads.example.org:invalid/app.exe"},
            {"url": "https://downloads.example.org:0/app.exe"},
            {"target": "macos-arm64"},
            {"published_at": "2026-02-30"},
            {"sha256": "not-a-digest"},
            {"size_bytes": True},
            {"size_bytes": 0},
            {"version": ""},
        ]
        mac = published()
        mac["url"] = "https://downloads.example.org/releases/1.2.3/macos-arm64.dmg"
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                windows = published()
                windows.update(overrides)
                catalog = self.catalog({"windows-x86_64": windows, "macos-arm64": mac})
                self.assertIsNone(catalog.downloads[0].release)
                self.assertEqual(catalog.downloads[1].release.url, mac["url"])
                self.assertTrue(catalog.readable)

    def test_unpublished_or_incomplete_records_never_create_links(self):
        for value in ({"published": True}, {**published(), "published": False},
                      {**published(), "published": "true"}):
            with self.subTest(value=value):
                catalog = self.catalog({"windows-x86_64": value})
                self.assertTrue(all(item.release is None for item in catalog.downloads))

    def test_missing_or_broken_catalog_keeps_page_and_return_available(self):
        for content in (None, "{broken", "[]", '{"format_version":2,"releases":{}}',
                        "[" * 1100 + "0" + "]" * 1100):
            with self.subTest(content=content):
                if content is None:
                    catalog = get_software_download_catalog(self.path)
                else:
                    self.path.write_text(content, encoding="utf-8")
                    catalog = get_software_download_catalog(self.path)
                self.assertFalse(catalog.readable)
                app = self.app(catalog)
                self.assertFalse(app.button(key="software_downloads_home").disabled)
                self.assertEqual(len(app.get("link_button")), 0)
                self.assertEqual(len(app.info), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
