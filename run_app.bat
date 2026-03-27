@echo off
setlocal
chcp 65001 >nul

set "PYTHON_EXE=C:\Users\gao_h\AppData\Local\Python\bin\python.exe"
set "SCRIPT_DIR=%~dp0"
set "APP_PORT=8506"

echo [LJQCApp] Starting app...
echo Local URL: http://127.0.0.1:%APP_PORT%
echo.

if not exist "%PYTHON_EXE%" (
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
