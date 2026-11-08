"""A small desktop update dialog; business application runs separately."""
from __future__ import annotations

from functools import partial
from pathlib import Path
import sys

from PySide6.QtCore import QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget)

from desktop_updates.bootstrap import (BootstrapError, default_base_path, helper_source,
                                       install_bootstrap)
from desktop_updates.helper_repair import helper_manifest_path, repair_registered_helper


class Worker(QThread):
    progress = Signal(object, object)

    def __init__(self, operation, parent=None, *, completed=None, failure=None):
        super().__init__(parent)
        self.operation = operation
        self.completed_callback = completed
        self.failure_callback = failure
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation(self.progress.emit)
        except Exception as error:
            # Manager/protocol exceptions contain customer-safe explanations;
            # unknown exceptions must not expose paths, code, or tracebacks.
            if isinstance(error, (RuntimeError, ValueError, OSError)):
                message = str(error)
                if (not any("\u4e00" <= character <= "\u9fff" for character in message)
                        or any(word in message for word in ("Traceback", "[Errno", ".py", "HTTPError"))):
                    message = "本次更新未完成，现有程序和资料已保留。请稍后重试。"
            else:
                message = "本次更新未完成，现有程序和资料已保留。请稍后重试。"
            self.error = message


def _disconnect_worker(worker, finished_slot, progress_slot=None):
    """Join the native thread before releasing any QObject/Python ownership."""
    worker.wait()
    worker.finished.disconnect(finished_slot)
    if progress_slot is not None:
        worker.progress.disconnect(progress_slot)
    outcome = (worker.completed_callback, worker.result, worker.failure_callback, worker.error)
    worker.operation = None
    worker.completed_callback = None
    worker.failure_callback = None
    worker.result = None
    worker.error = None
    return outcome


class UpdateWindow(QWidget):
    def __init__(self, manager, *, manual_check=False, confirm_update=None):
        super().__init__()
        self.manager = manager
        self.manual_check = manual_check
        self.confirm_update = confirm_update
        self.manifest = None
        self.archive = None
        self.worker = None
        self._closing = False
        self.setWindowTitle("LJQCApp 更新")
        self.resize(480, 300)
        layout = QVBoxLayout(self)
        self.version = QLabel()
        self.status = QLabel("正在检查更新……")
        self.status.setWordWrap(True)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        for widget in (self.version, self.status, self.notes, self.progress):
            layout.addWidget(widget)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("打开软件")
        self.check_button = QPushButton("检查更新")
        self.update_button = QPushButton("下载并更新")
        self.update_button.setVisible(False)
        self.open_button.clicked.connect(self.open_current)
        self.check_button.clicked.connect(self.check)
        self.update_button.clicked.connect(self.update)
        for button in (self.open_button, self.check_button, self.update_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._refresh_version()
        QApplication.instance().aboutToQuit.connect(self.wait_for_worker)
        QTimer.singleShot(0, self.check)

    def _refresh_version(self):
        state = self.manager.get_state()
        current = state.get("current", {})
        self.version.setText("当前版本 " + str(current.get("version", "1.00")))

    def _start(self, operation, completed, *, failure=None):
        if self.worker is not None and self.worker.isRunning():
            return
        for button in (self.open_button, self.check_button, self.update_button):
            button.setEnabled(False)
        self.worker = Worker(operation, self, completed=completed, failure=failure)
        self.worker.finished.connect(self._worker_finished)
        self.worker.progress.connect(self._progress)
        self.worker.start()

    @Slot()
    def _worker_finished(self):
        worker = self.worker
        if worker is None:
            return
        outcome = _disconnect_worker(worker, self._worker_finished, self._progress)
        self.worker = None
        worker.deleteLater()
        # No delayed callback retains the QThread. Joining above also covers
        # native thread-local teardown after Qt emits its finished signal.
        QTimer.singleShot(0, partial(self._dispatch_outcome, *outcome))

    def _dispatch_outcome(self, completed, result, failure, error):
        if self._closing:
            return
        if error is not None:
            self._failed(error, failure)
        else:
            self._finished(completed, result)

    @Slot()
    def wait_for_worker(self):
        self._closing = True
        worker = self.worker
        if worker is None:
            return
        _disconnect_worker(worker, self._worker_finished, self._progress)
        self.worker = None
        worker.deleteLater()

    def _finished(self, completed, result):
        for button in (self.open_button, self.check_button, self.update_button):
            button.setEnabled(True)
        self.progress.setVisible(False)
        completed(result)

    def _failed(self, message, failure):
        for button in (self.open_button, self.check_button, self.update_button):
            button.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText(message)
        if failure is not None:
            failure(message)

    @Slot(object, object)
    def _progress(self, current, total):
        if self._closing:
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, 100 if total else 0)
        if total:
            self.progress.setValue(min(100, int(current * 100 / total)))

    def check(self):
        self.status.setText("正在检查更新……")
        self._start(lambda progress: self.manager.check(), self._checked,
                    failure=(None if self.manual_check else lambda message: self.open_current()))

    def _checked(self, manifest):
        self.manifest = manifest
        if manifest is None:
            self.status.setText("当前已是可用的最新版本。")
            self.update_button.setVisible(False)
            if not self.manual_check:
                self.open_current()
            return
        self.status.setText("有可用更新：" + str(manifest.get("version", "")))
        description = manifest.get("release_notes") or manifest.get("notes") or manifest.get("summary") or "改进功能及稳定性。"
        if isinstance(description, list):
            description = "\n".join(str(line) for line in description)
        self.notes.setPlainText(str(description))
        self.notes.setVisible(True)
        self.update_button.setVisible(True)
        self.open_button.setText("稍后更新，打开软件")

    def update(self):
        if self.manifest is None:
            return
        confirmed = (self.confirm_update() if self.confirm_update is not None else
            QMessageBox.question(self, "更新 LJQCApp",
                "请先保存工作并正常关闭已打开的 LJQCApp。\n更新会保留现有资料，完成后重新打开软件。\n现在下载并更新吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes)
        if not confirmed:
            return
        self.status.setText("正在下载更新……")
        self.progress.setVisible(True)
        self._start(lambda progress: self.manager.download(self.manifest, progress=progress), self._downloaded)

    def _downloaded(self, archive):
        self.archive = archive
        self.status.setText("正在验证并更新，请勿关闭此窗口……")
        # A second worker avoids blocking the dialog while platform checks and
        # backups run. Manager owns locks and refuses a live business process.
        self._start(lambda progress: self.manager.apply(self.manifest, self.archive), self._applied)

    def _applied(self, result):
        self._refresh_version()
        self.status.setText("更新已完成。")
        self.open_current()

    def open_current(self):
        try:
            self.manager.launch_current(wait=False)
        except Exception:
            self.status.setText("程序未能打开，请保留现有资料并核对程序状态。")
            return
        self.close()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            self.status.setText("正在完成当前操作，请稍候。")
            return
        self._closing = True
        event.accept()


class BootstrapWindow(QWidget):
    def __init__(self, manager, *, base=None, helper=None, repair_mode=False):
        super().__init__()
        self.manager, self.helper, self.worker = manager, helper, None
        self._closing = False
        self.repair_mode = repair_mode
        self.next_window = None
        self.setWindowTitle("LJQCApp 修复更新组件" if repair_mode else "LJQCApp 更新接入")
        self.resize(520, 220)
        layout = QVBoxLayout(self)
        self.status = QLabel("修复更新组件后，通过原入口继续检查更新。当前程序、版本记录和资料会保留。"
            if repair_mode else "选择已安装的 LJQCApp 1.00。接入后通过原入口检查更新，现有资料继续保留。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.base = QLineEdit("" if repair_mode else str(base or default_base_path(manager.target)))
        self.browse = QPushButton("选择程序")
        self.browse.clicked.connect(self.choose_base)
        row.addWidget(self.base)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.base.setVisible(not repair_mode)
        self.browse.setVisible(not repair_mode)
        # A Mac may run a quarantined copy separately from its DMG sidecar.
        # Ask for the exact source application; never search the filesystem or
        # modify quarantine. The repair/adoption worker still verifies every
        # source byte and the complete macOS signature before changing entry.
        self.helper_source_missing = False
        self.helper_choice = QLabel()
        self.helper_choice.setWordWrap(True)
        self.choose_helper_button = QPushButton("选择更新接入应用")
        self.choose_helper_button.clicked.connect(self.choose_helper)
        if manager.target == "macos-arm64":
            source = self.helper or helper_source()
            sidecar = helper_manifest_path(Path(source), manager.target)
            self.helper_source_missing = not sidecar.exists() and not sidecar.is_symlink()
        self.helper_choice.setText("请从下载的更新接入补丁中选择 LJQCAppUpdater 应用。")
        self.helper_choice.setVisible(self.helper_source_missing)
        self.choose_helper_button.setVisible(self.helper_source_missing)
        layout.addWidget(self.helper_choice)
        layout.addWidget(self.choose_helper_button)
        self.install = QPushButton("修复更新组件" if repair_mode else "接入更新")
        self.install.setEnabled(not self.helper_source_missing)
        self.install.clicked.connect(self.adopt)
        layout.addWidget(self.install)
        QApplication.instance().aboutToQuit.connect(self.wait_for_worker)

    def choose_base(self):
        location = QFileDialog.getExistingDirectory(self, "选择已安装的 LJQCApp", self.base.text(),
            QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog | QFileDialog.DontResolveSymlinks)
        if location:
            self.base.setText(location)

    def choose_helper(self):
        location, _ = QFileDialog.getOpenFileName(self, "选择更新接入应用", "",
                                                  "更新接入应用 (LJQCAppUpdater.app)")
        if not location:
            return
        source = Path(location)
        sidecar = helper_manifest_path(source, "macos-arm64")
        if (source.name != "LJQCAppUpdater.app" or source.is_symlink() or not source.is_dir()
                or sidecar.is_symlink() or not sidecar.is_file()):
            self.helper_choice.setText("请直接选择下载补丁里的 LJQCAppUpdater 应用，并保持补丁内容完整。")
            return
        self.helper = source
        self.helper_source_missing = False
        self.helper_choice.setText("已选择 LJQCAppUpdater 应用。")
        self.install.setEnabled(True)

    def adopt(self):
        label = "修复更新组件" if self.repair_mode else "接入更新"
        instruction = ("请先保存工作，关闭原软件及此前打开的更新窗口，保留当前修复窗口。"
                       if self.repair_mode else "请先保存工作并正常关闭 LJQCApp。")
        if QMessageBox.question(self, label, instruction + "\n现在" + label + "吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.install.setEnabled(False)
        self.choose_helper_button.setEnabled(False)
        self.browse.setEnabled(False)
        self.base.setEnabled(False)
        self.status.setText("正在修复更新组件，请稍候……" if self.repair_mode else "正在核对原程序并接入更新，请稍候……")
        base = Path(self.base.text()).expanduser()
        try:
            helper = self.helper or helper_source()
        except Exception:
            self.failed("请使用对应系统的更新接入补丁进行安装。")
            return
        operation = ((lambda progress: repair_registered_helper(self.manager, helper)) if self.repair_mode
                     else (lambda progress: install_bootstrap(self.manager, base, helper)))
        self.worker = Worker(operation, self)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    @Slot()
    def _worker_finished(self):
        worker = self.worker
        if worker is None:
            return
        outcome = _disconnect_worker(worker, self._worker_finished)
        self.worker = None
        worker.deleteLater()
        QTimer.singleShot(0, partial(self._dispatch_outcome, outcome[1], outcome[3]))

    def _dispatch_outcome(self, result, error):
        if self._closing:
            return
        if error is not None:
            self.failed(error)
        else:
            self.completed(result)

    @Slot()
    def wait_for_worker(self):
        self._closing = True
        worker = self.worker
        if worker is None:
            return
        _disconnect_worker(worker, self._worker_finished)
        self.worker = None
        worker.deleteLater()

    def failed(self, message):
        self.status.setText(message)
        self.install.setEnabled(not self.helper_source_missing)
        self.choose_helper_button.setEnabled(True)
        self.browse.setEnabled(True)
        self.base.setEnabled(True)

    def completed(self, receipt):
        self.status.setText("更新组件已修复。以后通过原程序入口打开即可检查更新。" if self.repair_mode
                           else "更新已接入。以后通过原程序入口打开即可检查更新。")
        self.next_window = UpdateWindow(self.manager)
        self.next_window.show()
        QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            self._closing = True
            event.accept()


def run_update_ui(manager, *, install=False, base=None, helper=None, manual_check=False, repair_mode=False) -> int:
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("LJQCApp")
    if repair_mode or install or not manager.get_state().get("current"):
        window = BootstrapWindow(manager, base=base, helper=helper, repair_mode=repair_mode)
    else:
        window = UpdateWindow(manager, manual_check=manual_check)
    window.show()
    return application.exec()


def run_ui_self_test(*, timeout_ms: int = 8000) -> dict:
    """Exercise the frozen Qt/thread lifecycle without storage or networking.

    Build runners select an offscreen platform before launching the helper.
    This is intentionally self-contained: repository test modules are not
    bundled, and the real update manager/data root are never instantiated.
    """
    class ProbeManager:
        target = "windows-x86_64"
        def __init__(self):
            self.stages = []
            self.version = "1.0.0"

        def get_state(self):
            return {"current": {"version": self.version}}

        def check(self):
            self.stages.append("check")
            return {"version": "1.0.1", "notes": "窗口验收"}

        def download(self, manifest, progress=None):
            self.stages.append("download")
            if progress:
                progress(3_000_000_000, 4_000_000_000)
            return Path("memory-only-update-probe.tar.gz")

        def apply(self, manifest, archive):
            if archive != Path("memory-only-update-probe.tar.gz"):
                raise RuntimeError("窗口验收收到错误的内存资料。")
            self.stages.append("apply")
            self.version = "1.0.1"
            return {"status": "passed"}

        def launch_current(self, *, wait=False):
            self.stages.append("launch")

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("LJQCApp updater verification")
    previous_quit = application.quitOnLastWindowClosed()
    application.setQuitOnLastWindowClosed(False)
    manager = ProbeManager()
    repair_window = BootstrapWindow(manager, repair_mode=True)
    repair_fields = (repair_window.base.isHidden() and repair_window.browse.isHidden()
                     and repair_window.install.text() == "修复更新组件")
    repair_window.close()
    window = UpdateWindow(manager, confirm_update=lambda: True)
    result = {"format_version": 1, "product": "LJQCAppUpdater", "status": "failed",
              "native_gui_initialized": True, "business_storage_opened": False,
              "repair_mode_ui_fields_hidden": repair_fields}
    started = False
    finished = False
    watcher = QTimer()
    watcher.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)

    def finish(success, reason=""):
        nonlocal finished
        if finished:
            return
        finished = True
        watcher.stop()
        deadline.stop()
        worker_joined = window.worker is None
        window.wait_for_worker()
        window.close()
        result.update(status="passed" if success and repair_fields else "failed", stages=list(manager.stages),
                      ui_closed=not window.isVisible(), worker_joined=worker_joined,
                      program_version=manager.version)
        if reason:
            result["reason"] = reason
        application.quit()

    def inspect():
        nonlocal started
        if finished:
            return
        if not started and window.manifest is not None and window.worker is None:
            started = True
            window.update()
        if (manager.stages == ["check", "download", "apply", "launch"]
                and window.worker is None and not window.isVisible()):
            finish(True)

    watcher.timeout.connect(inspect)
    deadline.timeout.connect(partial(finish, False, "window-flow-timeout"))
    window.show()
    watcher.start()
    deadline.start(timeout_ms)
    try:
        application.exec()
        if not finished:
            finish(False, "window-flow-ended-early")
        return result
    finally:
        watcher.stop()
        deadline.stop()
        watcher.timeout.disconnect(inspect)
        window.wait_for_worker()
        window.close()
        window.deleteLater()
        application.setQuitOnLastWindowClosed(previous_quit)
