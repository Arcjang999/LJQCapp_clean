@echo off
setlocal
cd /d "%~dp0"
py tools\seed_demo_data.py --profile full --replace-demo
endlocal
