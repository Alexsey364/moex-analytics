@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Virtual environment .venv was not found. Creating it with Python 3.12...
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create .venv. Install Python 3.12 and try again.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" scripts\check_runtime_dependencies.py
if errorlevel 1 (
  pause
  exit /b 1
)
if not exist "database\market.duckdb" (
  echo [INFO] Database is absent. The dashboard will offer initial setup.
)
".venv\Scripts\python.exe" -m moex_analytics.dashboard.launcher
if errorlevel 3 goto launch_dashboard
if errorlevel 2 (
  pause
  exit /b 2
)
exit /b 0

:launch_dashboard
".venv\Scripts\python.exe" -m moex_analytics.cli dashboard
if errorlevel 1 (
  echo [ERROR] Dashboard failed to start. Check the messages above.
  pause
  exit /b 1
)
endlocal
