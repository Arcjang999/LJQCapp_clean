"""Offscreen Qt update flow with isolated manager doubles; no live app/data."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from desktop_updates.qt_ui import UpdateWindow


class FakeManager:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.launched = result, error, 0
        self.applied, self.downloaded = [], []

    def get_state(self):
        return {"current": {"version": "1.0.0"}}

    def check(self):
        if self.error:
            raise self.error
        return self.result

    def launch_current(self, *, wait):
        self.launched += 1

    def download(self, manifest, progress=None):
        self.downloaded.append(manifest)
        if progress:
            progress(3_000_000_000, 4_000_000_000)
        return Path("isolated-verified-patch.tar.gz")

    def apply(self, manifest, archive):
        self.applied.append((manifest, archive))
        return {"status": "passed"}


class UpdateUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.windows = []

    def window(self, manager, **kwargs):
        window = UpdateWindow(manager, **kwargs)
        self.windows.append(window)
        window.show()
        return window

    def tearDown(self):
        # Keep every window alive until all owned native threads are joined;
        # then drain deferred QObject destruction before the next test case.
        for window in self.windows:
            window.wait_for_worker()
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.windows.clear()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.005)
        self.fail("Qt update operation did not finish")

    def test_automatic_no_update_opens_existing_app(self):
        manager = FakeManager()
        window = self.window(manager)
        self.wait_until(lambda: manager.launched == 1 and not window.isVisible())
        self.assertIsNone(window.worker)

    def test_offline_does_not_block_existing_app(self):
        manager = FakeManager(error=RuntimeError("network unavailable"))
        window = self.window(manager)
        self.wait_until(lambda: manager.launched == 1 and not window.isVisible())
        self.assertNotIn("network", window.status.text())

    def test_manual_check_stays_open_without_starting_second_app(self):
        manager = FakeManager()
        window = self.window(manager, manual_check=True)
        self.wait_until(lambda: window.worker is None and "最新版本" in window.status.text())
        self.assertEqual(manager.launched, 0)
        self.assertTrue(window.isVisible())
        window.close()

    def test_download_apply_then_launch_uses_finished_worker(self):
        manager = FakeManager(result={"version": "1.0.1", "notes": "修复已知问题"})
        window = self.window(manager)
        self.wait_until(lambda: window.manifest is not None and window.worker is None)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window.update()
        self.wait_until(lambda: manager.launched == 1 and not window.isVisible())
        self.assertEqual(len(manager.downloaded), 1)
        self.assertEqual(len(manager.applied), 1)
        self.assertIsNone(window.worker)


if __name__ == "__main__":
    unittest.main()
