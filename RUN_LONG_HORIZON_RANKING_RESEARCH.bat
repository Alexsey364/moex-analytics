@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%CD%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo ERROR: project Python environment not found.
  exit /b 1
)
"%PYTHON_EXE%" -m moex_analytics.cli run-long-horizon-ranking-validation || exit /b 1
"%PYTHON_EXE%" -m moex_analytics.cli run-uncertainty-aware-rank-groups || exit /b 1
"%PYTHON_EXE%" -m moex_analytics.cli update-live-ranking-track-record || exit /b 1
"%PYTHON_EXE%" -m moex_analytics.cli audit-current-snapshot-freshness || exit /b 1
"%PYTHON_EXE%" -m moex_analytics.cli build-distilled-investor-view || exit /b 1
"%PYTHON_EXE%" -m moex_analytics.cli write-long-horizon-ranking-report || exit /b 1
echo Long-horizon ranking research completed.
exit /b 0
