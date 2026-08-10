@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :check_venv312
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if not errorlevel 1 goto :run

:check_venv312
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :missing
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if errorlevel 1 goto :missing

:run
"%PYTHON_EXE%" --version
if errorlevel 1 goto :error
"%PYTHON_EXE%" -m moex_analytics.cli run-historical-analog-research
if errorlevel 1 goto :error
echo Historical analog research completed. Production changes: 0.
exit /b 0

:missing
echo ERROR: Python 3.12 project environment was not found.
exit /b 1

:error
echo ERROR: Historical analog research failed. Re-run to resume checkpoints.
exit /b 1
