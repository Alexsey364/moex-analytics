@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" goto :no_python
".venv312\Scripts\python.exe" -m moex_analytics.cli ingest-live-news
if errorlevel 1 exit /b %errorlevel%
".venv312\Scripts\python.exe" -m moex_analytics.cli build-news-reaction-memory
if errorlevel 1 exit /b %errorlevel%
".venv312\Scripts\python.exe" -m moex_analytics.cli run-news-conditioned-research
exit /b %errorlevel%
:no_python
echo Python environment not found.
exit /b 1
