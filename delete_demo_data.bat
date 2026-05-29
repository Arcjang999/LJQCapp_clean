@echo off
setlocal
cd /d "%~dp0"
py tools\seed_demo_data.py --delete-demo
endlocal
