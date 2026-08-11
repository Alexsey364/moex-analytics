@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto run
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto run

echo ERROR: Python 3.12 virtual environment was not found.
exit /b 1

:run
"%PYTHON_EXE%" -m moex_analytics.market_marathon
if errorlevel 1 exit /b 1
exit /b 0
