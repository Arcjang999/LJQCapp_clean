@echo off
setlocal
cd /d "%~dp0"
echo This will delete ALL data in the configured LJQCApp database.
echo It will then import the full demo data profile.
echo.
set /p CONFIRM=Type RESET to continue:
if not "%CONFIRM%"=="RESET" (
    echo Cancelled.
    endlocal
    exit /b 1
)
py tools\seed_demo_data.py --profile full --reset-all --yes-reset-all
endlocal
