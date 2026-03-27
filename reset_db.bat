@echo off
setlocal
chcp 65001 >nul

set "PYTHON_EXE=C:\Users\gao_h\AppData\Local\Python\bin\python.exe"
set "SCRIPT_DIR=%~dp0"

echo [LJQCApp] Resetting database...
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python interpreter not found:
    echo %PYTHON_EXE%
    echo.
    echo Please check whether Python is installed correctly.
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT_DIR%reset_db.py"
if errorlevel 1 (
    echo.
    echo [FAILED] Database reset did not complete.
    echo Please close the running app and try again.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Database reset complete.
echo Please restart the app.
echo.
pause
exit /b 0
