from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


APP_VERSION = "1.0.0"


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

echo 此工具会清除当前用户本地 LJQCApp 数据库。
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
set "DB_DIR=%LOCALAPPDATA%\LJQCApp"
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
  echo 请双击“打不开先点我_修复运行环境.bat”，或双击“生成诊断包_发给开发者.bat”。
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
    return """LJQCApp 运行说明

正常使用：
双击 LJQCApp.exe。

如果打不开：
第一步：双击 打不开先点我_修复运行环境.bat
第二步：再双击 LJQCApp.exe
第三步：如果还是打不开，双击 备用启动_内置浏览器模式.bat
第四步：如果仍失败，再双击 备用启动_浏览器模式.bat
第五步：如果仍失败，双击 生成诊断包_发给开发者.bat，然后把桌面的 LJQCApp_诊断包.zip 发给开发者。

数据操作：
清空数据：双击 清除数据库.bat
写入演示数据：双击 写入演示数据.bat

浏览器说明：
不建议使用 IE。
不建议使用 360/搜狗的兼容模式。
如果浏览器页面空白，说明浏览器太旧或系统组件不完整。
备用启动_浏览器模式.bat 会先使用内置浏览器；如果改用本机浏览器，会先显示浏览器检测页。

老系统说明：
Windows 7、盗版系统、Ghost 精简系统可能缺少必要组件。
本兼容版默认使用随包携带的 QtWebEngine 桌面窗口，不要求电脑预装 Edge 或 WebView2。
Windows 7 属于过旧系统，本软件仅提供有限兼容，不保证稳定运行。
如果修复运行环境后仍无法启动，建议换 Windows 10/11 完整系统，或先使用备用浏览器模式。
最推荐 Windows 10/11 完整系统。

请不要手动查找系统日志。
请不要手动输入复杂命令。
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", required=True)
    parser.add_argument("--git-commit", default="")
    parser.add_argument("--build-time", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    staging = Path(args.staging)
    build_time = args.build_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    build_info = {
        "app_version": APP_VERSION,
        "build_time": build_time,
        "git_commit": args.git_commit or "",
        "desktop_shell": "QtWebEngine",
        "webview2_required": False,
        "default_browser_required": False,
        "browser_check_page": True,
        "browser_strategy": "bundled QtWebEngine first, installed modern browser second, default browser last",
        "win7_support": "limited",
        "x86_support": False,
        "x64_support": True,
    }

    write_text(staging / "清除数据库.bat", build_reset_bat())
    write_text(staging / "写入演示数据.bat", build_seed_bat())
    write_text(staging / "打不开先点我_修复运行环境.bat", build_repair_bat())
    write_text(staging / "备用启动_浏览器模式.bat", build_browser_bat())
    write_text(staging / "备用启动_内置浏览器模式.bat", build_internal_browser_bat())
    write_text(staging / "生成诊断包_发给开发者.bat", build_diagnose_bat())
    write_text(staging / "README_运行说明.txt", build_readme())
    write_text(staging / "_internal" / "browser_check.html", build_browser_check_html())
    write_text(staging / "_internal" / "app" / "browser_check.html", build_browser_check_html())
    write_json(staging / "_internal" / "build_info.json", build_info)
    (staging / "_internal" / "runtime").mkdir(parents=True, exist_ok=True)
    (staging / "_internal" / "logs").mkdir(parents=True, exist_ok=True)
    (staging / "_internal" / "runtime" / "README.txt").write_text(
        "可在此目录放置 WebView2 和 VC++ Runtime 安装包。\n"
        "文件名建议：MicrosoftEdgeWebView2RuntimeInstaller.exe、VC_redist.x64.exe、VC_redist.x86.exe。\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
