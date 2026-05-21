@echo off
setlocal
chcp 65001 >nul

call "%~dp0reset_db.bat"
exit /b %ERRORLEVEL%

