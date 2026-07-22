@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "APP_PORT=8506"

if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo [LJQCApp] Starting app...
echo Local URL: http://127.0.0.1:%APP_PORT%
echo.

"%PYTHON_EXE%" --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python interpreter not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

pushd "%SCRIPT_DIR%"
"%PYTHON_EXE%" -m streamlit run "app.py" --server.headless true --server.port %APP_PORT%
set "RUN_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%RUN_EXIT_CODE%"=="0" (
    echo.
    echo [INFO] App exited with code: %RUN_EXIT_CODE%
    echo.
    pause
    exit /b %RUN_EXIT_CODE%
)

pause
exit /b 0
