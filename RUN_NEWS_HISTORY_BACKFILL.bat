@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" goto :no_python
echo Historical news backfill uses only configured governed archives and is resumable.
".venv312\Scripts\python.exe" -m moex_analytics.cli ingest-live-news
exit /b %errorlevel%
:no_python
echo Python environment not found.
exit /b 1
