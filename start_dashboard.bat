@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment .venv was not found.
  echo python -m venv .venv
  echo .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  pause
  exit /b 1
)
if not exist "database\market.duckdb" (
  echo [INFO] Database is absent. The dashboard will offer initial setup.
)
".venv\Scripts\python.exe" -m moex_analytics.cli dashboard
if errorlevel 1 (
  echo [ERROR] Dashboard failed to start. Check the messages above.
  pause
  exit /b 1
)
endlocal
