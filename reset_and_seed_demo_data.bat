@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PYTHON_CMD="
set "PYTHON_ARG="

if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON_CMD=%SCRIPT_DIR%.venv\Scripts\python.exe"
)

if not defined PYTHON_CMD (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py"
    set "PYTHON_ARG=-3"
  )
)

if not defined PYTHON_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo [ERROR] Python was not found.
  echo Tried: .venv\Scripts\python.exe, py -3, python
  pause
  exit /b 1
)

echo [LJQCApp] Reset database and seed demo data...
echo [WARNING] This clears the whole database.
echo.

pushd "%SCRIPT_DIR%"
"%PYTHON_CMD%" %PYTHON_ARG% "%SCRIPT_DIR%run_app.py" --reset-and-seed-demo --profile full --yes
set "RUN_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%RUN_EXIT_CODE%"=="0" (
  echo [FAILED] Command failed. Exit code: %RUN_EXIT_CODE%
  echo Close the running app and try again.
  pause
  exit /b %RUN_EXIT_CODE%
)

echo [OK] Done.
pause
exit /b 0

