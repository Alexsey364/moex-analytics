@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [ERROR] Не найдено виртуальное окружение .venv312 или .venv.
  exit /b 1
)
"%PYTHON_EXE%" -m moex_analytics.cli quick-daily-update
if errorlevel 1 (
  echo [WARNING] Обновление завершилось с ошибкой. Dashboard может показать предыдущие данные.
  exit /b 1
)
echo.
echo Анализ завершён.
echo Откройте:
echo http://localhost:8501
endlocal
