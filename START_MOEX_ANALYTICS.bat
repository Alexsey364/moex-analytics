@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto check_venv312
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if not errorlevel 1 goto venv_ready

:check_venv312
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto create_venv
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if not errorlevel 1 goto venv_ready

:create_venv
py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 goto python_missing
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
py -3.12 -m venv "%CD%\.venv"
if errorlevel 1 goto venv_error

:venv_ready
"%PYTHON_EXE%" --version
if errorlevel 1 goto python_error
"%PYTHON_EXE%" scripts\check_runtime_dependencies.py
if errorlevel 1 goto dependency_error
"%PYTHON_EXE%" -m moex_analytics.launcher
if errorlevel 1 goto launcher_error
exit /b 0

:python_missing
echo ERROR: Python 3.12 was not found. Install it manually and retry.
goto fail

:venv_error
echo ERROR: Could not create the local Python 3.12 environment.
goto fail

:python_error
echo ERROR: The selected Python executable does not work.
goto fail

:dependency_error
echo ERROR: Required project dependencies are not available.
goto fail

:launcher_error
echo ERROR: MOEX Analytics launcher failed.
goto fail

:fail
pause
exit /b 1
