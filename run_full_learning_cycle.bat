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
if not errorlevel 1 goto :run

:missing
echo ERROR: Python 3.12 project environment was not found.
echo Run START_MOEX_ANALYTICS.bat first to validate dependencies.
pause
exit /b 1

:run
"%PYTHON_EXE%" --version
if errorlevel 1 goto :error
"%PYTHON_EXE%" -m moex_analytics.cli run-full-learning-cycle
if errorlevel 1 goto :error
echo Full research learning cycle completed. Production was not changed.
pause
exit /b 0

:error
echo ERROR: Full research learning cycle failed. Re-run to resume from checkpoints.
pause
exit /b 1
