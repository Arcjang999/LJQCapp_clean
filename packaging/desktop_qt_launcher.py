from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import traceback
from urllib.parse import quote


APP_NAME = "LJQCApp"
APP_VERSION = "1.0.0"
MAINTENANCE_FLAGS = {"--reset-db", "--seed-demo", "--diagnose", "--version"}
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


def _log_dir() -> Path:
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
    print(text)


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
    candidates = [
        _base_dir() / "_internal" / "app" / "LJQCAppService.exe",
        _base_dir() / "_internal" / "service" / "LJQCAppService.exe",
        _base_dir() / "LJQCAppService.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(str(candidates[0]))


def _is_maintenance_command(args: list[str]) -> bool:
    return any(arg in MAINTENANCE_FLAGS for arg in args)


def _run_service_command(args: list[str]) -> int:
    _attach_parent_console()
    try:
        service_path = _service_exe_path()
    except Exception:
        print("程序文件不完整，请重新解压完整 zip。")
        _write_log("Service executable is missing.")
        return 1

    command = [str(service_path), *args]
    _write_log(f"Running service command: {' '.join(command)}")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(service_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
        process.wait()
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
    if major == 6 and minor == 1:
        return (
            "当前系统是 Windows 7，系统过旧，本版本仅提供有限兼容。\n"
            "若无法启动，请使用 Windows 10/11 完整系统。"
        )
    if os.name == "nt" and major and major < 10:
        return (
            "当前 Windows 系统过旧，本版本仅提供有限兼容。\n"
            "若无法启动，请使用 Windows 10/11 完整系统。"
        )
    return ""


def _warn_legacy_windows_once() -> None:
    message = _legacy_windows_warning()
    if not message:
        return
    _write_log(f"Legacy Windows warning: {message}")
    _message_box(message)


def _configure_qt_environment() -> None:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    internal = _base_dir() / "_internal"
    candidates = [
        internal / "PySide6" / "Qt",
        internal / "PyQt5" / "Qt5",
        internal / "PyQt5" / "Qt",
    ]
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
        return False, "当前电脑是 32 位系统，此版本暂不支持。请联系开发者获取 32 位兼容包。"
    if not _test_localappdata_writable():
        return False, "当前电脑权限异常，无法写入本地数据目录。请把软件解压到普通文件夹后再试。"
    if not _test_loopback_bind():
        return False, "当前电脑无法启动本地服务端口。请关闭安全软件拦截后再试。"
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
    candidates = [
        _base_dir() / "_internal" / "browser_check.html",
        _base_dir() / "browser_check.html",
        _base_dir().parent / "browser_check.html",
        _base_dir().parent / "_internal" / "browser_check.html",
    ]
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
        else:
            subprocess.Popen(["xdg-open", check_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _write_log("Opened browser check page with system default browser as last fallback.")
        return True, "未找到明确的现代浏览器，已用系统默认浏览器打开检测页。若提示浏览器过旧，请改用内置浏览器模式。"
    except Exception:
        _write_log("Failed to open system default browser:")
        _write_log(traceback.format_exc())
        return False, "未找到可用浏览器。请双击【备用启动_内置浏览器模式.bat】或【打不开先点我_修复运行环境.bat】。"


def _start_service(port: int) -> subprocess.Popen[object]:
    service_path = _service_exe_path()
    command = [str(service_path), "--port", str(port), "--address", "127.0.0.1"]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_file = (_log_dir() / "qt_desktop_service.log").open("a", encoding="utf-8")
    _write_log(f"Starting service: {' '.join(command)}")
    return subprocess.Popen(
        command,
        cwd=str(service_path.parent),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )


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


def _stop_process_tree(process: subprocess.Popen[object] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.terminate()
    except Exception:
        _write_log("Failed to stop service process tree:")
        _write_log(traceback.format_exc())


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
        _message_box(
            "当前电脑缺少运行组件，已无法打开桌面窗口。\n"
            "请双击【打不开先点我_修复运行环境.bat】修复。\n"
            "如果修复后仍失败，请双击【备用启动_浏览器模式.bat】。"
        )
        return 1

    port = _get_free_loopback_port()
    process: subprocess.Popen[object] | None = None
    try:
        _write_log("Creating QApplication and QWebEngineView.")
        app = QApplication(sys.argv[:1])
        app.setApplicationName(APP_NAME)
        window = QMainWindow()
        window.setWindowTitle(APP_NAME)
        window.resize(1440, 960)
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
            _message_box(
                "LJQCApp 本地服务启动失败。\n"
                "请先双击【打不开先点我_修复运行环境.bat】。\n"
                "如果仍失败，请双击【备用启动_浏览器模式.bat】。"
            )
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
        _message_box(
            "当前电脑系统组件不完整，桌面窗口启动失败。\n"
            "请先双击【打不开先点我_修复运行环境.bat】。\n"
            "如果仍失败，请双击【备用启动_浏览器模式.bat】。"
        )
        return 1
    finally:
        _write_log("Shutting down service process tree.")
        _stop_process_tree(process)


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
        _stop_process_tree(process)


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
    if _is_maintenance_command(args):
        return _run_service_command(args)
    if "--browser-mode" in args:
        return _run_browser_mode(args)
    if "--internal-browser-mode" in args:
        return _run_qt_desktop(args)
    return _run_qt_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
