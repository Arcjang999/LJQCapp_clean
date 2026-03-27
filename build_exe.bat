@echo off
setlocal
chcp 65001 >nul

set "PYTHON_EXE=C:\Users\gao_h\AppData\Local\Python\bin\python.exe"
set "SCRIPT_DIR=%~dp0"
set "SPEC_FILE=%SCRIPT_DIR%LJQCApp.spec"
set "SAFE_BUILD_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_build"
set "SAFE_DIST_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_dist"
set "SAFE_PACKAGE_DIR=%SAFE_DIST_DIR%\LJQCApp"
set "PROJECT_DIST_DIR=%SCRIPT_DIR%dist\LJQCApp"

echo [LJQCApp] Building EXE package...
echo.

if not exist "%PYTHON_EXE%" (
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

pushd "%SCRIPT_DIR%"
if not exist "%SAFE_BUILD_DIR%" mkdir "%SAFE_BUILD_DIR%"
if not exist "%SAFE_DIST_DIR%" mkdir "%SAFE_DIST_DIR%"
"%PYTHON_EXE%" -m PyInstaller --clean -y --distpath "%SAFE_DIST_DIR%" --workpath "%SAFE_BUILD_DIR%" "LJQCApp.spec"
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
if not exist "%SCRIPT_DIR%dist" mkdir "%SCRIPT_DIR%dist"
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
