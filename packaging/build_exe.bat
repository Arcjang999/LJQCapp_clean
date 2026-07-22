@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SPEC_FILE=%SCRIPT_DIR%LJQCApp.spec"
set "SAFE_BUILD_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_build"
set "SAFE_DIST_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_dist"
set "SAFE_PACKAGE_DIR=%SAFE_DIST_DIR%\LJQCApp"
set "PROJECT_DIST_DIR=%PROJECT_ROOT%dist\LJQCApp"

if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo [LJQCApp] Building EXE package...
echo.

"%PYTHON_EXE%" --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python interpreter not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

if not exist "%SPEC_FILE%" (
    echo [ERROR] Spec file not found:
    echo %SPEC_FILE%
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller is not installed in this Python environment.
    echo Please run:
    echo "%PYTHON_EXE%" -m pip install pyinstaller
    echo.
    pause
    exit /b 1
)

pushd "%PROJECT_ROOT%"
if not exist "%SAFE_BUILD_DIR%" mkdir "%SAFE_BUILD_DIR%"
if not exist "%SAFE_DIST_DIR%" mkdir "%SAFE_DIST_DIR%"
"%PYTHON_EXE%" -m PyInstaller --clean -y --distpath "%SAFE_DIST_DIR%" --workpath "%SAFE_BUILD_DIR%" "%SPEC_FILE%"
set "BUILD_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%BUILD_EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] Build did not complete. Please review the log above.
    echo.
    pause
    exit /b %BUILD_EXIT_CODE%
)

if exist "%PROJECT_DIST_DIR%" rmdir /s /q "%PROJECT_DIST_DIR%"
if not exist "%PROJECT_ROOT%dist" mkdir "%PROJECT_ROOT%dist"
robocopy "%SAFE_PACKAGE_DIR%" "%PROJECT_DIST_DIR%" /MIR >nul
set "COPY_EXIT_CODE=%ERRORLEVEL%"
if %COPY_EXIT_CODE% GEQ 8 (
    echo.
    echo [FAILED] Build succeeded, but copying files back to project dist failed.
    echo Safe package folder: %SAFE_PACKAGE_DIR%
    echo.
    pause
    exit /b %COPY_EXIT_CODE%
)

echo.
echo [OK] Build completed successfully.
echo Output folder: dist\LJQCApp
echo Share the whole dist\LJQCApp folder with your teammates.
echo.
pause
exit /b 0
