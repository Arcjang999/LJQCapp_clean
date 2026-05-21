@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_CMD="
set "PYTHON_ARG="
set "SPEC_FILE=%SCRIPT_DIR%LJQCApp.spec"
set "SAFE_BUILD_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_build"
set "SAFE_DIST_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_dist"
set "SAFE_PACKAGE_DIR=%SAFE_DIST_DIR%\LJQCApp"
set "PROJECT_DIST_DIR=%PROJECT_ROOT%dist\LJQCApp"

if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
  set "PYTHON_CMD=%PROJECT_ROOT%\.venv\Scripts\python.exe"
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

echo [LJQCApp] Building EXE package...
echo.

if not defined PYTHON_CMD (
  echo [ERROR] Python was not found.
  echo Tried: .venv\Scripts\python.exe, py -3, python
  pause
  exit /b 1
)

if not exist "%SPEC_FILE%" (
  echo [ERROR] Spec file was not found:
  echo %SPEC_FILE%
  pause
  exit /b 1
)

"%PYTHON_CMD%" %PYTHON_ARG% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo [ERROR] PyInstaller is not installed.
  echo Install it with: "%PYTHON_CMD%" %PYTHON_ARG% -m pip install pyinstaller
  pause
  exit /b 1
)

pushd "%PROJECT_ROOT%"
if not exist "%SAFE_BUILD_DIR%" mkdir "%SAFE_BUILD_DIR%"
if not exist "%SAFE_DIST_DIR%" mkdir "%SAFE_DIST_DIR%"
set "LJQCAPP_APP_NAME=LJQCApp"
"%PYTHON_CMD%" %PYTHON_ARG% -m PyInstaller --clean -y --distpath "%SAFE_DIST_DIR%" --workpath "%SAFE_BUILD_DIR%" "%SPEC_FILE%"
set "BUILD_EXIT_CODE=%ERRORLEVEL%"
set "LJQCAPP_APP_NAME="
popd

if not "%BUILD_EXIT_CODE%"=="0" (
  echo [FAILED] Build failed. Exit code: %BUILD_EXIT_CODE%
  pause
  exit /b %BUILD_EXIT_CODE%
)

if exist "%PROJECT_DIST_DIR%" rmdir /s /q "%PROJECT_DIST_DIR%"
if not exist "%PROJECT_ROOT%dist" mkdir "%PROJECT_ROOT%dist"
robocopy "%SAFE_PACKAGE_DIR%" "%PROJECT_DIST_DIR%" /MIR >nul
set "COPY_EXIT_CODE=%ERRORLEVEL%"
if %COPY_EXIT_CODE% GEQ 8 (
  echo [FAILED] Build completed, but copy failed. Exit code: %COPY_EXIT_CODE%
  echo Safe package folder: %SAFE_PACKAGE_DIR%
  pause
  exit /b %COPY_EXIT_CODE%
)

echo [OK] Build completed successfully.
echo Output folder: dist\LJQCApp
pause
exit /b 0

