@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call START_MOEX_ANALYTICS.bat
exit /b %errorlevel%
