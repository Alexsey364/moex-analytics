@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo ERROR: Python environment not found. Run START_MOEX_ANALYTICS.bat first.
  exit /b 1
)

"%PYTHON_EXE%" -u -m moex_analytics.cli run-predictive-data-expansion
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo ERROR: Predictive data expansion stopped with code %EXIT_CODE%.
exit /b %EXIT_CODE%
