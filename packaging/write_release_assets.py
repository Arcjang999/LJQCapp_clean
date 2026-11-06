from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from services.release_info import get_release_info
from release_payload import write_payload_manifest

APP_VERSION = get_release_info().version


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("\n", "\r\n"), encoding="utf-8-sig")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_reset_bat() -> str:
    return r"""@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo 此工具会清除当前配置的 LJQCApp 数据库。
choice /C YN /M "确认清除数据库"
if errorlevel 2 (
  echo 已取消。
  echo.
  pause
  exit /b 0
)

"%~dp0LJQCApp.exe" --reset-db
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
  echo.
  echo 数据库已清除。
  echo.
  pause
  exit /b 0
)

echo.
echo LJQCApp 维护命令执行失败，正在尝试直接清理默认数据库文件...
set "DB_DIR=%~dp0data"
set "DB_FILE=%DB_DIR%\qc_lj_app.db"
if exist "%DB_FILE%" del /f /q "%DB_FILE%" >nul 2>nul
if exist "%DB_FILE%-wal" del /f /q "%DB_FILE%-wal" >nul 2>nul
if exist "%DB_FILE%-shm" del /f /q "%DB_FILE%-shm" >nul 2>nul
if exist "%DB_FILE%-journal" del /f /q "%DB_FILE%-journal" >nul 2>nul

if exist "%DB_FILE%" (
  echo 数据库清除失败。请先关闭 LJQCApp 后重试。
  echo.
  pause
  exit /b 1
)

echo 数据库已清除。
echo.
pause
exit /b 0
"""


def build_seed_bat() -> str:
    return r"""@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo 正在写入 LJQCApp 演示数据，请稍候...
"%~dp0LJQCApp.exe" --seed-demo --replace-demo --profile full
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 演示数据写入失败。请先关闭 LJQCApp 后重试。
  echo 如果仍失败，请双击“生成诊断包_发给开发者.bat”。
  echo.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo 演示数据已写入。再次打开 LJQCApp 后可以查看 LJ 和 Z-score 演示内容。
echo.
pause
exit /b 0
"""


def build_repair_bat() -> str:
    return r"""@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo 此工具会尝试安装 LJQCApp 所需运行环境。
echo 安装过程中可能弹出系统确认窗口，请点“允许”。
echo.

set "RUNTIME_DIR=%~dp0_internal\runtime"
set "WEBVIEW2=%RUNTIME_DIR%\MicrosoftEdgeWebView2RuntimeInstaller.exe"
set "VCX64=%RUNTIME_DIR%\VC_redist.x64.exe"
set "VCX86=%RUNTIME_DIR%\VC_redist.x86.exe"
set "HAS_INSTALLER=0"

if exist "%VCX64%" (
  set "HAS_INSTALLER=1"
  echo 正在安装或修复 VC++ Runtime x64...
  "%VCX64%" /install /passive /norestart
) else (
  echo 未找到 VC_redist.x64.exe
)

if exist "%VCX86%" (
  set "HAS_INSTALLER=1"
  echo 正在安装或修复 VC++ Runtime x86...
  "%VCX86%" /install /passive /norestart
) else (
  echo 未找到 VC_redist.x86.exe
)

if exist "%WEBVIEW2%" (
  set "HAS_INSTALLER=1"
  echo 正在安装或修复 Microsoft Edge WebView2 Runtime...
  "%WEBVIEW2%" /silent /install
) else (
  echo 未找到 MicrosoftEdgeWebView2RuntimeInstaller.exe
)

echo.
if "%HAS_INSTALLER%"=="0" (
  echo 安装包缺失，请联系开发者重新获取完整 zip。
) else (
  echo 运行环境修复流程已执行完成。
)

echo.
choice /C YN /M "是否现在启动 LJQCApp"
if errorlevel 2 (
  echo 稍后可手动双击 LJQCApp.exe 启动。
  echo.
  pause
  exit /b 0
)

start "" "%~dp0LJQCApp.exe"
echo 已尝试启动 LJQCApp。
echo.
pause
exit /b 0
"""


def build_browser_bat() -> str:
    return r"""@echo off
setlocal
chcp 65001 >nul
title LJQCApp 备用启动模式，请勿关闭
cd /d "%~dp0"

echo 正在启动 LJQCApp 备用浏览器模式。
echo 将优先使用随包携带的内置浏览器；如内置浏览器不可用，再搜索本机现代浏览器。
echo 不会主动使用 Internet Explorer。
echo 请不要关闭本窗口；关闭后软件会停止运行。
echo.

"%~dp0LJQCApp.exe" --browser-mode
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 备用浏览器模式启动失败。
  echo 请双击“生成诊断包_发给开发者.bat”，然后把桌面的诊断包发给开发者。
)

echo.
pause
exit /b %EXIT_CODE%
"""


def build_internal_browser_bat() -> str:
    return r"""@echo off
setlocal
chcp 65001 >nul
title LJQCApp 内置浏览器模式
cd /d "%~dp0"

echo 正在启动 LJQCApp 内置浏览器模式。
echo 此模式使用随包携带的 QtWebEngine，不依赖 Edge、WebView2、Chrome 或系统默认浏览器。
echo.

"%~dp0LJQCApp.exe" --internal-browser-mode
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 内置浏览器模式启动失败。
  echo 请重新运行安装程序，或双击“生成诊断包_发给开发者.bat”。
)

echo.
pause
exit /b %EXIT_CODE%
"""


def build_diagnose_bat() -> str:
    return r"""@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo 正在生成 LJQCApp 诊断包，请稍候...
"%~dp0LJQCApp.exe" --diagnose
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 诊断包生成失败，请重新解压完整 zip 后再试。
  echo.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo 诊断包已生成在桌面，请把 LJQCApp_诊断包.zip 发给开发者。
echo.
pause
exit /b 0
"""


def build_readme() -> str:
    return """LJQCApp Windows 运行说明

适用系统：Windows 10/11 64 位。
安装后从桌面或开始菜单打开 LJQCApp，首次使用按窗口中的引导建立项目。
本机内置浏览器随程序安装，无需另行安装 Python、Edge 或 WebView2。

资料保存：
默认资料保存在当前用户的 LocalAppData\\LJQCApp 目录。
在软件“系统设置”选择的保存位置继续生效；安装、更新和普通卸载保留资料。
升级前请在“系统设置”生成完整备份，再关闭软件并运行新安装包。
已有旧版资料时，请按交付说明明确选择原软件的 data 文件夹完成接入。

打不开时：
重新运行安装程序。仍无法打开时，运行安装目录中的“生成诊断包_发给开发者.bat”。
“备用启动_浏览器模式.bat”提供另一种打开方式；使用结束后按窗口提示停止服务。
诊断包可能包含本机配置，请核对后再交给维护人员。

关闭软件后请等待本机服务正常退出，再安装其他版本。
"""


def build_browser_check_html() -> str:
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
        msg.innerHTML = "当前浏览器太旧或处于兼容模式，请关闭此页面并从桌面快捷方式重新打开软件。";
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--target", default="windows-x86_64")
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--build-time", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    staging = Path(args.staging)
    build_info = (json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata
                  else get_release_info(include_source_state=True).as_dict())
    if build_info.get("target") not in (None, "", args.target):
        raise ValueError("Build metadata target differs from the requested payload")
    if not build_info.get("version") or not build_info.get("source_digest"):
        raise ValueError("A version and source input digest are required for a release payload")
    if args.git_commit and not str(build_info.get("git_commit", "")).startswith(args.git_commit):
        raise ValueError("Legacy Git argument differs from the canonical build metadata")
    build_info.update({
        "app_version": build_info["version"],
        "build_time": build_info.get("build_time") or args.build_time or datetime.now(timezone.utc).isoformat(),
        "target": args.target,
        "desktop_shell": "QtWebEngine",
        "webview2_required": False,
        "default_browser_required": False,
        "browser_check_page": True,
        "browser_strategy": "bundled QtWebEngine first, installed modern browser second, default browser last",
        "win7_support": False,
        "x86_support": False,
        "x64_support": True,
        "supported_windows": ["10", "11"],
        "business_data_included": False,
    })

    write_text(staging / "备用启动_浏览器模式.bat", build_browser_bat())
    write_text(staging / "备用启动_内置浏览器模式.bat", build_internal_browser_bat())
    write_text(staging / "生成诊断包_发给开发者.bat", build_diagnose_bat())
    write_text(staging / "README_运行说明.txt", build_readme())
    write_text(staging / "_internal" / "browser_check.html", build_browser_check_html())
    write_text(staging / "_internal" / "app" / "browser_check.html", build_browser_check_html())
    for location in (staging, staging / "_internal", staging / "_internal" / "app",
                     staging / "_internal" / "app" / "_internal"):
        write_json(location / "build_info.json", build_info)
    write_payload_manifest(staging, build_info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
