@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto missing
"%PYTHON_EXE%" -m moex_analytics.launcher --daily-only
exit /b %errorlevel%

:missing
echo ERROR: Run START_MOEX_ANALYTICS.bat to prepare the environment.
exit /b 1
