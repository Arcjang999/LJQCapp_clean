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

echo [LJQCApp] 导入 full profile 演示数据...
echo 旧的【演示】数据会先删除；真实数据不会被删除。
echo.

pushd "%SCRIPT_DIR%"
"%PYTHON_EXE%" %PYTHON_ARGS% "%SCRIPT_DIR%tools\seed_demo_data.py" --profile full --replace-demo
set "RUN_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%RUN_EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] 演示数据导入失败，错误码：%RUN_EXIT_CODE%
    echo 请确认依赖已安装："%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r requirements.txt
    echo.
    pause
    exit /b %RUN_EXIT_CODE%
)

echo.
echo [OK] 演示数据导入完成。
echo.
pause
exit /b 0
