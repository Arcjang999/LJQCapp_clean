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
    echo.
    echo 请先安装 Python 3，或在项目目录创建 .venv 后重试。
    echo 探测顺序：.venv\Scripts\python.exe ^> py -3 ^> python
    echo.
    pause
    exit /b 1
)

echo [LJQCApp] Resetting database...
echo Python: "%PYTHON_EXE%" %PYTHON_ARGS%
echo.

pushd "%SCRIPT_DIR%"
"%PYTHON_EXE%" %PYTHON_ARGS% "%SCRIPT_DIR%reset_db.py"
set "RUN_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%RUN_EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] 数据库重置未完成。
    echo 请先关闭正在运行的应用，再重试。
    echo.
    pause
    exit /b %RUN_EXIT_CODE%
)

echo.
echo [OK] 数据库重置完成，请重新启动应用。
echo.
pause
exit /b 0
