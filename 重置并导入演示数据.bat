@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE="
set "PYTHON_ARGS="

if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
)
if not defined PYTHON_EXE (
    py -3 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3"
    )
)
if not defined PYTHON_EXE (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    echo [ERROR] 未找到可用的 Python。
    echo 请先安装 Python 3，或在项目目录创建 .venv 后重试。
    pause
    exit /b 1
)

echo [危险操作] 这会清空整个数据库，然后导入标准 full profile 演示数据。
echo 真实质控数据也会被删除。
echo.
set /p CONFIRM=如已备份并确认继续，请输入 YES 后回车：
if /i not "%CONFIRM%"=="YES" (
    echo 已取消。
    pause
    exit /b 0
)

pushd "%SCRIPT_DIR%"
"%PYTHON_EXE%" %PYTHON_ARGS% "%SCRIPT_DIR%tools\seed_demo_data.py" --reset-all --yes --profile full
set "RUN_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%RUN_EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] 重置并导入演示数据失败，错误码：%RUN_EXIT_CODE%
    echo 请确认已关闭正在运行的应用，并确认依赖已安装。
    echo.
    pause
    exit /b %RUN_EXIT_CODE%
)

echo.
echo [OK] 数据库已恢复为标准演示状态。
echo.
pause
exit /b 0
