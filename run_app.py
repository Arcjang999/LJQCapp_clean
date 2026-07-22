from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.parse import quote
import zipfile


APP_VERSION = "1.0.0"
DEFAULT_SERVER_ADDRESS = "127.0.0.1"
DEFAULT_SERVER_PORT = int(os.environ.get("LJQCAPP_PORT", "8501"))
OLD_BROWSER_MESSAGE = (
    "当前浏览器太旧，无法运行 LJQCApp。"
    "请使用内置浏览器模式，或换 Windows 10/11 完整系统。"
)

BROWSER_CANDIDATES = [
    ("Microsoft Edge Chromium", "%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe", ["--new-window"]),
    ("Microsoft Edge Chromium", "%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe", ["--new-window"]),
    ("Microsoft Edge Chromium", "%LOCALAPPDATA%\\Microsoft\\Edge\\Application\\msedge.exe", ["--new-window"]),
    ("Google Chrome", "%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe", ["--new-window"]),
    ("Google Chrome", "%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe", ["--new-window"]),
    ("Google Chrome", "%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe", ["--new-window"]),
    ("Firefox", "%ProgramFiles%\\Mozilla Firefox\\firefox.exe", ["-new-window"]),
    ("Firefox", "%ProgramFiles(x86)%\\Mozilla Firefox\\firefox.exe", ["-new-window"]),
    ("360 极速浏览器", "%ProgramFiles%\\360\\360Chrome\\Chrome\\Application\\360chrome.exe", []),
    ("360 极速浏览器", "%ProgramFiles(x86)%\\360\\360Chrome\\Chrome\\Application\\360chrome.exe", []),
    ("360 极速浏览器", "%LOCALAPPDATA%\\360Chrome\\Chrome\\Application\\360chrome.exe", []),
    ("360 安全浏览器", "%ProgramFiles%\\360\\360se6\\Application\\360se.exe", []),
    ("360 安全浏览器", "%ProgramFiles(x86)%\\360\\360se6\\Application\\360se.exe", []),
    ("搜狗高速浏览器", "%ProgramFiles%\\SogouExplorer\\SogouExplorer.exe", []),
    ("搜狗高速浏览器", "%ProgramFiles(x86)%\\SogouExplorer\\SogouExplorer.exe", []),
    ("搜狗高速浏览器", "%LOCALAPPDATA%\\SogouExplorer\\SogouExplorer.exe", []),
    ("QQ 浏览器", "%ProgramFiles%\\Tencent\\QQBrowser\\QQBrowser.exe", []),
    ("QQ 浏览器", "%ProgramFiles(x86)%\\Tencent\\QQBrowser\\QQBrowser.exe", []),
    ("QQ 浏览器", "%LOCALAPPDATA%\\Tencent\\QQBrowser\\QQBrowser.exe", []),
]


def _get_log_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        log_dir = Path(local_app_data) / "LJQCApp"
    else:
        log_dir = Path.home() / ".ljqcapp"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _get_log_path() -> Path:
    return _get_log_dir() / "launcher.log"


def _write_log(message: str) -> None:
    log_path = _get_log_path()
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(message.rstrip() + "\n")


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        extracted_dir = getattr(sys, "_MEIPASS", "")
        if extracted_dir:
            return Path(str(extracted_dir)).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_app_path(base_dir: Path) -> Path:
    candidate_paths = [
        base_dir / "app.py",
        base_dir / "_internal" / "app.py",
    ]
    for candidate_path in candidate_paths:
        if candidate_path.exists():
            return candidate_path.resolve()
    return candidate_paths[0].resolve()


def _resolve_build_info_path(base_dir: Path) -> Path | None:
    for candidate in [
        base_dir / "build_info.json",
        base_dir / "_internal" / "build_info.json",
        Path(sys.executable).resolve().parent / "build_info.json",
        Path(sys.executable).resolve().parent / "_internal" / "build_info.json",
    ]:
        if candidate.exists():
            return candidate
    return None


def _read_build_info() -> dict[str, object]:
    info_path = _resolve_build_info_path(_get_base_dir())
    if info_path is None:
        return {}
    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _configure_import_paths(base_dir: Path, app_path: Path) -> None:
    import_roots = [app_path.parent, base_dir]
    for import_root in import_roots:
        import_root_str = str(import_root)
        if import_root_str not in sys.path:
            sys.path.insert(0, import_root_str)


def _build_streamlit_argv(app_path: Path, *, port: int, address: str) -> list[str]:
    return [
        "streamlit",
        "run",
        str(app_path),
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
        "--client.showSidebarNavigation=false",
        "--server.fileWatcherType=none",
        "--server.headless=true",
        f"--server.address={address}",
        f"--server.port={port}",
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or maintain the packaged LJQCApp Streamlit service.")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT)
    parser.add_argument("--address", default=DEFAULT_SERVER_ADDRESS)
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--replace-demo", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--diagnose-output", default="")
    parser.add_argument("--browser-mode", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--demo-profile", "--profile", dest="demo_profile", choices=("basic", "full"), default="full")
    return parser.parse_args(argv)


def reset_configured_database() -> int:
    import database

    db_path = database.get_db_path()
    try:
        database.reset_database()
    except Exception:
        error_trace = traceback.format_exc()
        print("数据库清除失败。请先关闭正在运行的 LJQCApp 后重试。")
        print(f"数据库路径：{db_path}")
        _write_log("Database reset failed:")
        _write_log(error_trace)
        return 1

    print("数据库已清除。")
    print(f"数据库路径：{db_path}")
    print("下次启动 LJQCApp 时会自动重新初始化数据库。")
    _write_log(f"Database reset completed: {db_path}")
    return 0


def seed_demo_database(*, profile: str, replace_demo: bool) -> int:
    try:
        from services.demo_data_service import seed_demo_data, validate_demo_data

        result = seed_demo_data(
            profile=profile,
            replace_demo=replace_demo,
            reset_all=False,
            dry_run=False,
        )
        validation = validate_demo_data(profile=profile)
    except Exception:
        error_trace = traceback.format_exc()
        print("演示数据写入失败。请先关闭正在运行的 LJQCApp 后重试。")
        _write_log("Demo seed failed:")
        _write_log(error_trace)
        return 1

    print("演示数据已写入。")
    print(f"演示数据档案：{result.profile}")
    print(f"写入数据集数量：{len(result.datasets)}")
    print(f"已验证月报包数量：{validation.checked_report_packages}")
    _write_log(
        "Demo seed completed: "
        f"profile={result.profile}, datasets={len(result.datasets)}, "
        f"report_packages={validation.checked_report_packages}"
    )
    return 0


def print_version() -> int:
    build_info = _read_build_info()
    print(f"LJQCApp 版本：{build_info.get('app_version') or APP_VERSION}")
    if build_info.get("build_time"):
        print(f"构建时间：{build_info['build_time']}")
    if build_info.get("git_commit"):
        print(f"Git commit：{build_info['git_commit']}")
    return 0


def run_streamlit_service(*, port: int, address: str) -> int:
    base_dir = _get_base_dir()
    app_path = _resolve_app_path(base_dir)
    log_path = _get_log_path()

    _write_log("=" * 80)
    _write_log(f"sys.executable: {sys.executable}")
    _write_log(f"cwd: {Path.cwd()}")
    _write_log(f"base_dir: {base_dir}")
    _write_log(f"resolved app.py path: {app_path}")
    _write_log(f"server.address: {address}")
    _write_log(f"server.port: {port}")

    if not app_path.exists():
        message = f"ERROR: app.py not found. Expected path: {app_path}. Log file: {log_path}"
        print("启动失败：程序文件不完整，请重新解压完整 zip。")
        _write_log(message)
        return 1

    os.chdir(base_dir)
    _configure_import_paths(base_dir, app_path)
    sys.argv = _build_streamlit_argv(app_path, port=port, address=address)
    try:
        from streamlit.web.cli import main as streamlit_main

        return streamlit_main()
    except Exception:
        error_trace = traceback.format_exc()
        print("启动失败：本地服务无法启动，请关闭 LJQCApp 后重试。")
        _write_log("Unhandled Streamlit exception:")
        _write_log(error_trace)
        return 1


def _get_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _build_self_service_command(port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [
            str(Path(sys.executable).resolve()),
            "--port",
            str(port),
            "--address",
            "127.0.0.1",
        ]
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--port",
        str(port),
        "--address",
        "127.0.0.1",
    ]


def _wait_for_health(port: int, *, process: subprocess.Popen[object] | None = None, timeout_seconds: int = 90) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/_stcore/health"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= int(response.status) < 300:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def _stop_process_tree(process: subprocess.Popen[object] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.terminate()


def _is_excluded_browser_path(path: Path | str) -> bool:
    lowered = str(path).lower()
    return "iexplore.exe" in lowered or "microsoftedge.exe" in lowered


def _find_modern_browsers() -> list[tuple[str, Path, list[str]]]:
    found: list[tuple[str, Path, list[str]]] = []
    seen: set[str] = set()
    for label, path_template, arguments in BROWSER_CANDIDATES:
        path = Path(os.path.expandvars(path_template))
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and not _is_excluded_browser_path(path):
            found.append((label, path, list(arguments)))
    return found


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


def _browser_check_file() -> Path:
    base_dir = _get_base_dir()
    candidates = [
        base_dir / "browser_check.html",
        base_dir / "_internal" / "browser_check.html",
        base_dir.parent / "browser_check.html",
        base_dir.parent / "_internal" / "browser_check.html",
        base_dir.parent.parent / "_internal" / "browser_check.html",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    fallback = _get_log_dir() / "browser_check.html"
    fallback.write_text(_browser_check_html(), encoding="utf-8")
    return fallback


def _browser_check_url(target_url: str) -> str:
    return f"{_browser_check_file().resolve().as_uri()}?target={quote(target_url, safe='')}"


def _default_browser_command_is_legacy() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        return False
    lowered_prog_id = str(prog_id).lower()
    if "ie." in lowered_prog_id or "microsoftedge" in lowered_prog_id:
        return True
    for hive, key_path in [
        (winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command"),
    ]:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                command, _ = winreg.QueryValueEx(key, "")
            lowered_command = str(command).lower()
            if "iexplore.exe" in lowered_command or "microsoftedge.exe" in lowered_command:
                return True
        except OSError:
            continue
    return False


def _open_browser_check_page(target_url: str) -> tuple[bool, str]:
    check_url = _browser_check_url(target_url)
    for label, path, arguments in _find_modern_browsers():
        try:
            subprocess.Popen([str(path), *arguments, check_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _write_log(f"Browser mode launched modern browser: {label} | {path}")
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
        return True, "未找到明确的现代浏览器，已用系统默认浏览器打开检测页。若提示浏览器过旧，请改用内置浏览器模式。"
    except Exception:
        _write_log("Failed to open browser check page:")
        _write_log(traceback.format_exc())
        return False, "未找到可用浏览器。请双击【备用启动_内置浏览器模式.bat】或【打不开先点我_修复运行环境.bat】。"


def run_browser_mode() -> int:
    port = _get_free_loopback_port()
    command = _build_self_service_command(port)
    log_path = _get_log_dir() / "browser_mode_service.log"
    print("正在启动 LJQCApp 备用浏览器模式，请稍候...")
    print("请不要关闭本窗口；关闭本窗口后，软件会停止运行。")
    _write_log(f"Browser mode command: {' '.join(command)}")

    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(_get_base_dir()),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        if not _wait_for_health(port, process=process, timeout_seconds=90):
            print("备用浏览器模式启动失败。请运行“生成诊断包_发给开发者.bat”。")
            _write_log("Browser mode health check failed.")
            return 1
        url = f"http://127.0.0.1:{port}"
        print(f"已启动本地服务：{url}")
        opened, message = _open_browser_check_page(url)
        print(message)
        if not opened:
            return 1
        process.wait()
        return int(process.returncode or 0)
    except KeyboardInterrupt:
        print("正在关闭 LJQCApp 备用浏览器模式...")
        return 0
    finally:
        _stop_process_tree(process)


def _detect_webview2() -> str:
    if os.name != "nt":
        return "非 Windows 系统，未检测。"
    try:
        import winreg
    except ImportError:
        return "无法读取注册表。"

    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ]
    for hive, key_path in candidates:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if str(version).strip():
                    return f"已检测到 WebView2 Runtime，版本：{version}"
        except OSError:
            continue
    return "未检测到 WebView2 Runtime。"


def _detect_vc_runtime() -> str:
    if os.name != "nt":
        return "非 Windows 系统，未检测。"
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        system_root / "System32" / "vcruntime140.dll",
        system_root / "System32" / "vcruntime140_1.dll",
        system_root / "SysWOW64" / "vcruntime140.dll",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    if existing:
        return "疑似已安装 VC++ Runtime：" + "; ".join(existing)
    return "未在系统目录检测到 VC++ Runtime。"


def _collect_text_diagnostics() -> str:
    build_info = _read_build_info()
    lines = [
        "LJQCApp 诊断信息",
        "=" * 60,
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"软件版本：{build_info.get('app_version') or APP_VERSION}",
        f"构建时间：{build_info.get('build_time') or '-'}",
        f"Git commit：{build_info.get('git_commit') or '-'}",
        f"系统：{platform.platform()}",
        f"Windows 版本：{platform.version()}",
        f"系统架构：{platform.machine()}",
        f"Python 架构：{platform.architecture()[0]}",
        f"当前用户：{os.environ.get('USERNAME') or os.environ.get('USER') or '-'}",
        f"当前路径：{Path.cwd()}",
        f"程序路径：{sys.executable}",
        f"LOCALAPPDATA：{os.environ.get('LOCALAPPDATA') or '-'}",
        f"是否疑似从压缩包直接运行：{'是' if '.zip' in str(Path.cwd()).lower() else '否'}",
        "",
        "WebView2 检测：",
        _detect_webview2(),
        "",
        "VC++ Runtime 检测：",
        _detect_vc_runtime(),
    ]
    return "\n".join(lines) + "\n"


def _default_diagnose_zip_path() -> Path:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    if not desktop.exists():
        desktop = Path.home()
    return desktop / "LJQCApp_诊断包.zip"


def generate_diagnostics(output_path: str = "") -> int:
    target_zip = Path(output_path).expanduser() if output_path.strip() else _default_diagnose_zip_path()
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    log_dir = _get_log_dir()

    try:
        with tempfile.TemporaryDirectory(prefix="ljqcapp_diag_") as tempdir:
            temp_root = Path(tempdir)
            (temp_root / "system_info.txt").write_text(_collect_text_diagnostics(), encoding="utf-8")

            build_info = _read_build_info()
            (temp_root / "build_info.json").write_text(
                json.dumps(build_info, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logs_root = temp_root / "logs"
            logs_root.mkdir()
            if log_dir.exists():
                for log_file in log_dir.glob("*.log"):
                    shutil.copy2(log_file, logs_root / log_file.name)

            with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                for file_path in temp_root.rglob("*"):
                    if file_path.is_file():
                        zip_file.write(file_path, file_path.relative_to(temp_root).as_posix())
    except Exception:
        error_trace = traceback.format_exc()
        print("诊断包生成失败。请重新解压完整 zip 后再试。")
        _write_log("Diagnose failed:")
        _write_log(error_trace)
        return 1

    print(f"诊断包已生成：{target_zip}")
    print("请把桌面的 LJQCApp_诊断包.zip 发给开发者。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        return print_version()
    if args.reset_db:
        return reset_configured_database()
    if args.seed_demo:
        return seed_demo_database(profile=args.demo_profile, replace_demo=bool(args.replace_demo))
    if args.diagnose:
        return generate_diagnostics(args.diagnose_output)
    if args.browser_mode:
        return run_browser_mode()
    return run_streamlit_service(port=args.port, address=args.address)


if __name__ == "__main__":
    raise SystemExit(main())
