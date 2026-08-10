@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto :run
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto :run
echo ERROR: Python 3.12 project environment was not found.
exit /b 1

:run
"%PYTHON_EXE%" -m moex_analytics.cli run-predictive-research-marathon --max-runtime-hours 10
if errorlevel 1 goto :error
echo Predictive research marathon completed. Production changes: 0.
exit /b 0

:error
echo ERROR: Predictive marathon failed. Re-run this file to resume checkpoints.
exit /b 1
