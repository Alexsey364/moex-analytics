@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [ERROR] Не найдено виртуальное окружение .venv312 или .venv.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -m moex_analytics.dashboard.launcher
if errorlevel 3 goto update_and_start
if errorlevel 2 (
  pause
  exit /b 2
)
start "" "http://localhost:8501"
exit /b 0

:update_and_start
start "MOEX Analytics" /min "%PYTHON_EXE%" -m moex_analytics.cli dashboard
ping 127.0.0.1 -n 6 >nul
start "" "http://localhost:8501"
start "MOEX Daily Analysis" /min cmd /c call run_daily_analysis.bat
endlocal
