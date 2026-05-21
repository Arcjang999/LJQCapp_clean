@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE="
set "PYTHON_ARGS="
set "SPEC_FILE=%SCRIPT_DIR%LJQCApp.spec"
set "SAFE_BUILD_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_build"
set "SAFE_DIST_DIR=%LOCALAPPDATA%\LJQCApp\pyinstaller_dist"
set "SAFE_PACKAGE_DIR=%SAFE_DIST_DIR%\LJQCApp"
set "PROJECT_DIST_DIR=%PROJECT_ROOT%dist\LJQCApp"

if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
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

echo [LJQCApp] Building EXE package...
echo.

if not defined PYTHON_EXE (
    echo [ERROR] 未找到可用的 Python。
    echo.
    echo 请先安装 Python 3，或在项目目录创建 .venv 后重试。
    echo 探测顺序：.venv\Scripts\python.exe ^> py -3 ^> python
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

"%PYTHON_EXE%" %PYTHON_ARGS% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 当前 Python 环境未安装 PyInstaller。
    echo 请运行：
    echo "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install pyinstaller
    echo.
    pause
    exit /b 1
)

pushd "%PROJECT_ROOT%"
if not exist "%SAFE_BUILD_DIR%" mkdir "%SAFE_BUILD_DIR%"
if not exist "%SAFE_DIST_DIR%" mkdir "%SAFE_DIST_DIR%"
set "LJQCAPP_APP_NAME=LJQCApp"
"%PYTHON_EXE%" %PYTHON_ARGS% -m PyInstaller --clean -y --distpath "%SAFE_DIST_DIR%" --workpath "%SAFE_BUILD_DIR%" "%SPEC_FILE%"
set "BUILD_EXIT_CODE=%ERRORLEVEL%"
set "LJQCAPP_APP_NAME="
popd

if not "%BUILD_EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] 打包未完成，请查看上方日志。
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
    echo [FAILED] 打包成功，但复制产物回项目 dist 目录失败。
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
