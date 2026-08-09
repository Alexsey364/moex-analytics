@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo ERROR: Python environment not found. Run START_MOEX_ANALYTICS.bat first.
  exit /b 1
)
"%PYTHON_EXE%" -u -m moex_analytics.cli run-clean-data-relearning
exit /b %ERRORLEVEL%
