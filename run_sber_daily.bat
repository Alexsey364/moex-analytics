@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m moex_analytics.cli run-sber-daily
) else (
  python -m moex_analytics.cli run-sber-daily
)
endlocal
