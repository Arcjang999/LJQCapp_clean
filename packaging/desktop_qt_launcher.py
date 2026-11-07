from __future__ import annotations

import argparse
import ctypes
import errno
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import traceback
import uuid
from urllib.parse import quote

from services.release_info import get_release_info
from services.storage_paths import get_storage_paths


APP_NAME = "LJQCApp"
APP_VERSION = get_release_info().version
MAINTENANCE_FLAGS = {"--reset-db", "--seed-demo", "--diagnose", "--version", "--adopt-from"}
WINDOWS_APP_MUTEX = r"Local\LJQCApp.Desktop"
_SERVICE_SHUTDOWN_FILES: dict[int, Path] = {}
OLD_BROWSER_MESSAGE = (
    "当前浏览器太旧，无法运行 LJQCApp。\n"
    "请使用内置浏览器模式，或换 Windows 10/11 完整系统。"
)

BROWSER_CANDIDATES = [
    ("Microsoft Edge Chromium", ["%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"], ["--new-window"]),
    ("Microsoft Edge Chromium", ["%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"], ["--new-window"]),
    ("Microsoft Edge Chromium", ["%LOCALAPPDATA%\\Microsoft\\Edge\\Application\\msedge.exe"], ["--new-window"]),
    ("Google Chrome", ["%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe"], ["--new-window"]),
    ("Google Chrome", ["%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe"], ["--new-window"]),
    ("Google Chrome", ["%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe"], ["--new-window"]),
    ("Firefox", ["%ProgramFiles%\\Mozilla Firefox\\firefox.exe"], ["-new-window"]),
    ("Firefox", ["%ProgramFiles(x86)%\\Mozilla Firefox\\firefox.exe"], ["-new-window"]),
    ("360 极速浏览器", ["%ProgramFiles%\\360\\360Chrome\\Chrome\\Application\\360chrome.exe"], []),
    ("360 极速浏览器", ["%ProgramFiles(x86)%\\360\\360Chrome\\Chrome\\Application\\360chrome.exe"], []),
    ("360 极速浏览器", ["%LOCALAPPDATA%\\360Chrome\\Chrome\\Application\\360chrome.exe"], []),
    ("360 安全浏览器", ["%ProgramFiles%\\360\\360se6\\Application\\360se.exe"], []),
    ("360 安全浏览器", ["%ProgramFiles(x86)%\\360\\360se6\\Application\\360se.exe"], []),
    ("搜狗高速浏览器", ["%ProgramFiles%\\SogouExplorer\\SogouExplorer.exe"], []),
    ("搜狗高速浏览器", ["%ProgramFiles(x86)%\\SogouExplorer\\SogouExplorer.exe"], []),
    ("搜狗高速浏览器", ["%LOCALAPPDATA%\\SogouExplorer\\SogouExplorer.exe"], []),
    ("QQ 浏览器", ["%ProgramFiles%\\Tencent\\QQBrowser\\QQBrowser.exe"], []),
    ("QQ 浏览器", ["%ProgramFiles(x86)%\\Tencent\\QQBrowser\\QQBrowser.exe"], []),
    ("QQ 浏览器", ["%LOCALAPPDATA%\\Tencent\\QQBrowser\\QQBrowser.exe"], []),
]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _resource_roots() -> list[Path]:
    """Resource locations for Windows onedir and the outer macOS app bundle."""
    base = _base_dir()
    roots = [base / "_internal", base]
    if base.name == "MacOS" and base.parent.name == "Contents":
        roots.insert(0, base.parent / "Resources")
        roots.append(base.parent / "Frameworks")
    extracted = getattr(sys, "_MEIPASS", None)
    if extracted:
        roots.append(Path(extracted))
    return list(dict.fromkeys(roots))


def _service_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["LJQC_DEPLOYMENT_MODE"] = "desktop"
    paths = get_storage_paths()
    if paths.uses_stable_root:
        env["LJQC_DATA_ROOT"] = str(paths.root)
    return env


def _log_dir() -> Path:
    paths = get_storage_paths()
    if paths.uses_stable_root:
        path = paths.root / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        path = Path(local_app_data) / "LJQCApp"
    else:
        path = Path.home() / ".ljqcapp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log_path() -> Path:
    return _log_dir() / "desktop_launcher.log"


def _write_log(message: str) -> None:
    with _log_path().open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message.rstrip()}\n")


def _message_box(text: str, title: str = APP_NAME) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x00000010)
        return
    if sys.platform == "darwin":
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            application = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, title, text)
            return
        except Exception:
            pass
    print(text)


def _startup_help() -> str:
    if os.name == "nt":
        return "请重新运行 Windows 安装程序；仍无法打开时，运行安装目录中的“生成诊断包_发给开发者.bat”。"
    return "请重新安装完整的 Mac 应用；仍无法打开时，按安装包中的运行说明生成诊断资料。"


class _DesktopInstanceLock:
    """A held file lock plus the Windows installer-visible named mutex."""

    def __init__(self) -> None:
        self.handle = None
        self.mutex = None
        self.kernel = None

    def acquire(self) -> bool:
        if os.name == "nt":
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.mutex = self.kernel.CreateMutexW(None, True, WINDOWS_APP_MUTEX)
            if not self.mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                self.kernel.CloseHandle(self.mutex)
                self.mutex = None
                return False
        paths = get_storage_paths()
        directory = (paths.root if paths.uses_stable_root else _log_dir()) / "runtime"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = (directory / "desktop.lock").open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.release()
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise
        return True

    def release(self) -> None:
        if self.handle is not None:
            # Closing releases the OS lock. Keep the inode to avoid a lock race.
            self.handle.close()
            self.handle = None
        if self.mutex is not None and self.kernel is not None:
            self.kernel.ReleaseMutex(self.mutex)
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None


def _attach_parent_console() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.AttachConsole(-1)
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        return


def _service_exe_path() -> Path:
    name = "LJQCAppService.exe" if os.name == "nt" else "LJQCAppService"
    candidates = [root / folder / name for root in _resource_roots() for folder in ("app", "service", "")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(str(candidates[0]))


def _is_maintenance_command(args: list[str]) -> bool:
    return any(arg in MAINTENANCE_FLAGS for arg in args)


def _run_service_command(args: list[str]) -> int:
    _attach_parent_console()
    try:
        service_path = _service_exe_path()
    except Exception:
        print("程序文件不完整，请重新安装对应系统的软件包。")
        _write_log("Service executable is missing.")
        return 1

    command = [str(service_path), *args]
    _write_log(f"Running service command: {' '.join(command)}")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(service_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_service_environment(),
        )
        assert process.stdout is not None
        for line in process.stdout:
            _write_log(line.rstrip())
            if sys.stdout is not None:
                print(line, end="")
        process.wait()
        _write_log(f"Service command exited with code {process.returncode}.")
        return int(process.returncode or 0)
    except Exception:
        print("维护命令执行失败。请关闭 LJQCApp 后重试。")
        _write_log("Maintenance command failed:")
        _write_log(traceback.format_exc())
        return 1


def _get_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _test_localappdata_writable() -> bool:
    try:
        probe_path = _log_dir() / "write_test.tmp"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _test_loopback_bind() -> bool:
    try:
        _get_free_loopback_port()
        return True
    except OSError:
        return False


def _system_summary() -> str:
    return f"{platform.platform()} | {platform.machine()} | Python {platform.architecture()[0]}"


def _windows_version_tuple() -> tuple[int, int, int]:
    if os.name != "nt":
        return (0, 0, 0)
    version = sys.getwindowsversion()
    return (int(version.major), int(version.minor), int(version.build))


def _legacy_windows_warning() -> str:
    major, minor, _build = _windows_version_tuple()
    if os.name == "nt" and major and major < 10:
        return (
            "本安装包支持 Windows 10/11 64 位。请在支持的系统上安装。"
        )
    return ""


def _warn_legacy_windows_once() -> None:
    message = _legacy_windows_warning()
    if not message:
        return
    _write_log(f"Legacy Windows warning: {message}")
    _message_box(message)


def _configure_qt_environment() -> None:
    # macOS bundle hooks locate QtWebEngineProcess and signed framework resources.
    # Windows retains the existing packaged-browser setting.
    if os.name == "nt":
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    candidates = [root / package / qt for root in _resource_roots()
                  for package, qt in (("PySide6", "Qt"), ("PyQt5", "Qt5"), ("PyQt5", "Qt"))]
    for qt_root in candidates:
        process_path = qt_root / "bin" / "QtWebEngineProcess.exe"
        resources_path = qt_root / "resources"
        locales_path = qt_root / "translations" / "qtwebengine_locales"
        if process_path.exists():
            os.environ.setdefault("QTWEBENGINEPROCESS_PATH", str(process_path))
        if resources_path.exists():
            os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", str(resources_path))
        if locales_path.exists():
            os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", str(locales_path))


def _qt_webengine_available() -> bool:
    if os.environ.get("LJQCAPP_DISABLE_INTERNAL_BROWSER") == "1":
        _write_log("Internal browser disabled by LJQCAPP_DISABLE_INTERNAL_BROWSER=1.")
        return False
    try:
        _configure_qt_environment()
        from PySide6.QtCore import QTimer, QUrl  # noqa: F401
        from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except Exception:
        _write_log("PySide6 QtWebEngine availability check failed:")
        _write_log(traceback.format_exc())
        return False


def _validate_environment() -> tuple[bool, str]:
    if platform.architecture()[0] != "64bit":
        return False, "本安装包需要 64 位系统。"
    if os.name == "nt" and _windows_version_tuple()[0] < 10:
        return False, "本安装包支持 Windows 10/11 64 位。"
    if sys.platform == "darwin" and platform.machine().lower() not in ("arm64", "aarch64"):
        return False, "此 Mac 安装包适用于 Apple 芯片（M 系列）。"
    if not _test_localappdata_writable():
        return False, "无法写入软件资料目录，请检查当前用户的文件夹权限。"
    if not _test_loopback_bind():
        return False, "无法启动本机服务，请检查系统是否允许本软件使用本机网络。"
    return True, ""


def _expand_env_path(path_template: str) -> Path:
    expanded = os.path.expandvars(path_template)
    return Path(expanded)


def _is_excluded_browser_path(path: Path | str) -> bool:
    lowered = str(path).lower()
    blocked_names = ("iexplore.exe", "microsoftedge.exe")
    return any(lowered.endswith(name) or f"\\{name}" in lowered for name in blocked_names)


def _find_modern_browsers() -> list[tuple[str, Path, list[str]]]:
    if os.environ.get("LJQCAPP_DISABLE_MODERN_BROWSER_SEARCH") == "1":
        _write_log("Modern browser search disabled by LJQCAPP_DISABLE_MODERN_BROWSER_SEARCH=1.")
        return []
    found: list[tuple[str, Path, list[str]]] = []
    seen: set[str] = set()
    for label, path_templates, arguments in BROWSER_CANDIDATES:
        for path_template in path_templates:
            path = _expand_env_path(path_template)
            key = str(path).casefold()
            if key in seen:
                continue
            seen.add(key)
            if not path.exists() or _is_excluded_browser_path(path):
                continue
            found.append((label, path, list(arguments)))
    return found


def _browser_check_file() -> Path:
    candidates = [root / "browser_check.html" for root in _resource_roots()]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    fallback = _log_dir() / "browser_check.html"
    fallback.write_text(_browser_check_html(), encoding="utf-8")
    return fallback


def _browser_check_url(target_url: str) -> str:
    return f"{_browser_check_file().resolve().as_uri()}?target={quote(target_url, safe='')}"


def _browser_check_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>LJQCApp 浏览器检测</title>
  <style>
    body { font-family: "Microsoft YaHei", Arial, sans-serif; margin: 48px; line-height: 1.8; color: #1f2933; }
    .box { max-width: 720px; border: 1px solid #d8dee9; padding: 24px; border-radius: 8px; }
    h1 { font-size: 22px; margin-top: 0; }
    .bad { color: #b42318; font-weight: 700; }
    .ok { color: #067647; font-weight: 700; }
  </style>
</head>
<body>
  <div class="box">
    <h1>LJQCApp 浏览器检测</h1>
    <p id="message">正在检测浏览器，请稍候...</p>
  </div>
  <script>
    (function () {
      var msg = document.getElementById("message");
      var ua = navigator.userAgent || "";
      var isOld = /MSIE|Trident|Edge\\/1[0-8]\\./i.test(ua);
      var missing = [];
      if (!("WebSocket" in window)) missing.push("WebSocket");
      if (!("Promise" in window)) missing.push("Promise");
      if (!("fetch" in window)) missing.push("fetch");
      try {
        var key = "__ljqcapp_check__";
        window.localStorage.setItem(key, "1");
        window.localStorage.removeItem(key);
      } catch (e) {
        missing.push("localStorage");
      }
      if (isOld || missing.length) {
        msg.className = "bad";
        msg.innerHTML = "当前浏览器太旧或处于兼容模式，无法运行 LJQCApp。请关闭此页面，双击【备用启动_内置浏览器模式.bat】或【打不开先点我_修复运行环境.bat】。";
        return;
      }
      var target = "";
      try {
        target = new URLSearchParams(window.location.search).get("target") || "";
      } catch (e) {
        target = "";
      }
      if (!target) {
        msg.className = "bad";
        msg.innerHTML = "未找到 LJQCApp 本地地址，请重新启动软件。";
        return;
      }
      msg.className = "ok";
      msg.innerHTML = "浏览器检测通过，正在打开 LJQCApp...";
      window.location.replace(target);
    })();
  </script>
</body>
</html>
"""


def _default_browser_command_is_legacy() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    prog_id = ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        return False
    prog_id_lower = str(prog_id).lower()
    if "ie." in prog_id_lower or "microsoftedge" in prog_id_lower:
        return True
    key_paths = [
        rf"Software\Classes\{prog_id}\shell\open\command",
        rf"{prog_id}\shell\open\command",
    ]
    hives = [winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT]
    for hive in hives:
        for key_path in key_paths:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    command, _ = winreg.QueryValueEx(key, "")
                lowered = str(command).lower()
                if "iexplore.exe" in lowered or "microsoftedge.exe" in lowered:
                    return True
            except OSError:
                continue
    return False


def _open_external_browser_with_check(target_url: str) -> tuple[bool, str]:
    check_url = _browser_check_url(target_url)
    for label, path, arguments in _find_modern_browsers():
        command = [str(path), *arguments, check_url]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _write_log(f"External browser launched: {label} | {path}")
            return True, f"已使用本机现代浏览器打开检测页：{label}"
        except Exception:
            _write_log(f"Failed to launch browser candidate: {label} | {path}")
            _write_log(traceback.format_exc())

    if _default_browser_command_is_legacy():
        _write_log("Default browser appears to be IE or Edge Legacy; refusing to open it.")
        return False, OLD_BROWSER_MESSAGE

    try:
        if os.name == "nt":
            os.startfile(check_url)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", check_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", check_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _write_log("Opened browser check page with system default browser as last fallback.")
        return True, "未找到明确的现代浏览器，已用系统默认浏览器打开检测页。若提示浏览器过旧，请改用内置浏览器模式。"
    except Exception:
        _write_log("Failed to open system default browser:")
        _write_log(traceback.format_exc())
        return False, "未找到可用浏览器。" + _startup_help()


def _start_service(port: int) -> subprocess.Popen[object]:
    service_path = _service_exe_path()
    command = [str(service_path), "--port", str(port), "--address", "127.0.0.1"]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_file = (_log_dir() / "qt_desktop_service.log").open("a", encoding="utf-8")
    paths = get_storage_paths()
    runtime_dir = (paths.root if paths.uses_stable_root else _log_dir()) / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutdown_file = runtime_dir / ("shutdown-" + uuid.uuid4().hex + ".request")
    if shutdown_file.exists():
        raise RuntimeError("A new service shutdown request path must not already exist")
    env = _service_environment()
    env["LJQC_SHUTDOWN_FILE"] = str(shutdown_file)
    _write_log(f"Starting service: {' '.join(command)}")
    try:
        process = subprocess.Popen(command, cwd=str(service_path.parent), stdin=subprocess.DEVNULL, stdout=log_file,
            stderr=subprocess.STDOUT, text=True, creationflags=creationflags, env=env,
            start_new_session=(os.name != "nt"))
    finally:
        log_file.close()
    _SERVICE_SHUTDOWN_FILES[process.pid] = shutdown_file
    return process


def _wait_for_service(port: int, process: subprocess.Popen[object], timeout_seconds: int = 90) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/_stcore/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _write_log(f"Service exited early with code {process.returncode}.")
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= int(response.status) < 300:
                    _write_log(f"Service became healthy on port {port}.")
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    _write_log("Service health check timed out.")
    return False


def _stop_process_tree(process: subprocess.Popen[object] | None, timeout_seconds: float = 15) -> bool:
    if process is None:
        return True
    shutdown_file = _SERVICE_SHUTDOWN_FILES.get(process.pid)
    if process.poll() is not None:
        _SERVICE_SHUTDOWN_FILES.pop(process.pid, None)
        if shutdown_file:
            shutdown_file.unlink(missing_ok=True)
        return True
    try:
        if shutdown_file:
            shutdown_file.write_text("stop\n", encoding="ascii")
        elif os.name != "nt":
            process.terminate()
        else:
            _write_log("Service has no graceful shutdown channel; waiting without forced termination.")
            return False
        process.wait(timeout=timeout_seconds)
        _SERVICE_SHUTDOWN_FILES.pop(process.pid, None)
        if shutdown_file:
            shutdown_file.unlink(missing_ok=True)
        return True
    except subprocess.TimeoutExpired:
        _write_log("Service has not finished shutting down; update and replacement remain blocked.")
        return False
    except Exception:
        _write_log("Failed to stop service process tree:")
        _write_log(traceback.format_exc())
        return False


def _finish_service_before_exit(process: subprocess.Popen[object] | None) -> None:
    if _stop_process_tree(process):
        return
    # Keep this launcher (and its instance/installer locks) alive while the
    # service finishes. Releasing the locks would allow a competing install.
    _write_log("Waiting for the service to release its files before leaving the launcher.")
    _message_box("本机服务尚未关闭，软件将继续等待。请等待保存结束后再安装其他版本。")
    if process is not None:
        process.wait()
        _stop_process_tree(process)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--auto-close-seconds", type=int, default=0)
    parser.add_argument("--browser-mode", action="store_true")
    parser.add_argument("--internal-browser-mode", action="store_true")
    return parser.parse_known_args(argv)[0]


def _run_qt_desktop(argv: list[str]) -> int:
    options = _parse_args(argv)
    _write_log("=" * 80)
    _write_log(f"Launcher starting. {_system_summary()}")

    ok, message = _validate_environment()
    if not ok:
        _write_log(f"Environment validation failed: {message}")
        _message_box(message)
        return 1
    _warn_legacy_windows_once()

    try:
        _configure_qt_environment()
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception:
        _write_log("PySide6 QtWebEngine import failed:")
        _write_log(traceback.format_exc())
        _message_box("当前电脑缺少运行组件，无法打开桌面窗口。\n" + _startup_help())
        return 1

    port = _get_free_loopback_port()
    process: subprocess.Popen[object] | None = None
    try:
        _write_log("Creating QApplication and QWebEngineView.")
        app = QApplication(sys.argv[:1])
        app.setApplicationName(APP_NAME)
        class DesktopWindow(QMainWindow):
            def closeEvent(self, event):
                if not _stop_process_tree(process):
                    _message_box("软件还在完成保存和关闭，请稍后再关闭窗口。")
                    event.ignore()
                    return
                event.accept()

        window = DesktopWindow()
        window.setWindowTitle(APP_NAME)
        window.resize(1440, 960)
        helper = os.environ.get('LJQC_UPDATE_HELPER', '').strip()
        if helper:
            def check_for_updates():
                from PySide6.QtWidgets import QMessageBox
                path = Path(helper)
                if not path.is_absolute() or not path.is_file() or path.is_symlink():
                    QMessageBox.information(window, APP_NAME, '更新组件未找到，请重新运行更新接入补丁。')
                    return
                env = _service_environment()
                env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
                try:
                    subprocess.Popen([str(path), '--check'], env=env, cwd=str(path.parent))
                except OSError:
                    QMessageBox.information(window, APP_NAME, '暂时无法打开更新组件，请保存并退出后重新打开软件。')
            window.menuBar().addMenu('帮助').addAction('检查更新', check_for_updates)
        view = QWebEngineView()
        window.setCentralWidget(view)
        active_downloads: list[object] = []

        def handle_download(download: object) -> None:
            try:
                suggested_name = ""
                if hasattr(download, "downloadFileName"):
                    suggested_name = str(download.downloadFileName() or "")
                if not suggested_name and hasattr(download, "suggestedFileName"):
                    suggested_name = str(download.suggestedFileName() or "")
                if not suggested_name:
                    suggested_name = "LJQCApp_report.pdf"
                suggested_name = Path(suggested_name).name or "LJQCApp_report.pdf"

                downloads_dir = Path.home() / "Downloads"
                if not downloads_dir.exists():
                    downloads_dir = Path.home()
                default_path = str((downloads_dir / suggested_name).resolve())
                selected_path, _ = QFileDialog.getSaveFileName(
                    window,
                    "保存报告",
                    default_path,
                    "PDF 文件 (*.pdf);;所有文件 (*.*)",
                )
                if not selected_path:
                    _write_log(f"Download canceled by user: {suggested_name}")
                    if hasattr(download, "cancel"):
                        download.cancel()
                    return

                target_path = Path(selected_path)
                if hasattr(download, "setDownloadDirectory"):
                    download.setDownloadDirectory(str(target_path.parent))
                if hasattr(download, "setDownloadFileName"):
                    download.setDownloadFileName(target_path.name)
                active_downloads.append(download)
                if hasattr(download, "accept"):
                    download.accept()
                _write_log(f"Download accepted: {target_path}")
            except Exception:
                _write_log("Failed to handle QtWebEngine download request:")
                _write_log(traceback.format_exc())
                try:
                    if hasattr(download, "cancel"):
                        download.cancel()
                except Exception:
                    pass

        view.page().profile().downloadRequested.connect(handle_download)

        process = _start_service(port)
        if not _wait_for_service(port, process):
            _message_box("本机服务启动失败。\n" + _startup_help())
            return 1

        _write_log(f"Loading QtWebEngine URL http://127.0.0.1:{port}")
        view.load(QUrl(f"http://127.0.0.1:{port}"))
        window.show()
        _write_log(f"QtWebEngine window attached to http://127.0.0.1:{port}")

        if options.auto_close_seconds > 0:
            QTimer.singleShot(options.auto_close_seconds * 1000, window.close)

        exit_code = int(app.exec())
        return exit_code
    except Exception:
        _write_log("Desktop launcher failed:")
        _write_log(traceback.format_exc())
        _message_box("桌面窗口启动失败。\n" + _startup_help())
        return 1
    finally:
        _write_log("Shutting down service process tree.")
        _finish_service_before_exit(process)


def _run_external_browser_mode(argv: list[str]) -> int:
    options = _parse_args(argv)
    _attach_parent_console()
    _write_log("=" * 80)
    _write_log(f"External browser mode starting. {_system_summary()}")
    print("正在启动 LJQCApp 备用浏览器模式。")
    print("此模式会先搜索现代浏览器，并先打开浏览器检测页；不会主动使用 Internet Explorer。")

    ok, message = _validate_environment()
    if not ok:
        print(message)
        _message_box(message)
        return 1
    _warn_legacy_windows_once()

    port = _get_free_loopback_port()
    process: subprocess.Popen[object] | None = None
    try:
        process = _start_service(port)
        if not _wait_for_service(port, process):
            print("LJQCApp 本地服务启动失败，请运行“生成诊断包_发给开发者.bat”。")
            return 1

        target_url = f"http://127.0.0.1:{port}"
        opened, launch_message = _open_external_browser_with_check(target_url)
        print(launch_message)
        if not opened:
            _message_box(launch_message)
            return 1

        print(f"本地服务地址：{target_url}")
        if options.auto_close_seconds > 0:
            time.sleep(options.auto_close_seconds)
            return 0
        print("请不要关闭本窗口；关闭本窗口后，软件会停止运行。")
        try:
            input("需要停止备用浏览器模式时，请按 Enter：")
        except EOFError:
            process.wait()
        return 0
    except KeyboardInterrupt:
        print("正在关闭 LJQCApp 备用浏览器模式...")
        return 0
    except Exception:
        _write_log("External browser mode failed:")
        _write_log(traceback.format_exc())
        print("备用浏览器模式启动失败。请运行“生成诊断包_发给开发者.bat”。")
        return 1
    finally:
        _write_log("Shutting down external browser mode service process tree.")
        _finish_service_before_exit(process)


def _run_browser_mode(argv: list[str]) -> int:
    _attach_parent_console()
    print("正在启动 LJQCApp 备用浏览器模式。")
    print("优先使用随包携带的内置浏览器引擎，不依赖系统默认浏览器。")
    if _qt_webengine_available():
        print("正在使用内置浏览器模式...")
        return _run_qt_desktop(argv)
    print("内置浏览器不可用，改为搜索本机现代浏览器。")
    return _run_external_browser_mode(argv)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(flag in args for flag in ("--version", "--diagnose")):
        return _run_service_command(args)
    lock = _DesktopInstanceLock()
    try:
        if not lock.acquire():
            _message_box("软件已经运行，请先关闭已有窗口，再打开或升级。")
            return 1
        if _is_maintenance_command(args):
            return _run_service_command(args)
        if "--browser-mode" in args:
            return _run_browser_mode(args)
        if "--internal-browser-mode" in args:
            return _run_qt_desktop(args)
        return _run_qt_desktop(args)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
