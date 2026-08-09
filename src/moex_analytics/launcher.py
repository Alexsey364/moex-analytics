"""Windows-safe human launcher; batch files remain thin ASCII wrappers."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from moex_analytics.dashboard import launcher as port_launcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_IMPORTS = (
    "duckdb",
    "streamlit",
    "pandas",
    "numpy",
    "sklearn",
    "plotly",
    "pyarrow",
    "scipy",
    "joblib",
    "requests",
    "yaml",
    "openpyxl",
)


def environment_errors() -> list[str]:
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append(f"Требуется Python 3.12, выбран {sys.version_info.major}.{sys.version_info.minor}.")
    missing = [name for name in REQUIRED_IMPORTS if importlib.util.find_spec(name) is None]
    if missing:
        errors.append("Отсутствуют обязательные библиотеки: " + ", ".join(missing))
    return errors


def run_quick_daily() -> bool:
    print("Обновление последних данных...", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "moex_analytics.cli", "quick-daily-update"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if result.returncode:
        print("Предупреждение: обновление не завершено; dashboard покажет последние сохранённые данные.")
    return result.returncode == 0


def _healthy() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:8501/_stcore/health", timeout=2) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except OSError:
        return False


def wait_until_healthy(timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy():
            return True
        time.sleep(0.25)
    return False


def start_dashboard() -> tuple[bool, str]:
    owner = port_launcher.port_owner()
    status = port_launcher.classify_owner(owner)
    if status == "dashboard":
        return True, "Dashboard уже работает: http://localhost:8501"
    if status == "other":
        return False, f"Порт 8501 занят другим процессом. PID: {owner[0]}"
    port_launcher._mark_pending()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, "-m", "moex_analytics.cli", "dashboard"],
        cwd=PROJECT_ROOT,
        creationflags=flags,
        close_fds=True,
    )
    if not wait_until_healthy():
        return False, "Dashboard не ответил на health-check за 30 секунд."
    return True, "Dashboard запущен: http://localhost:8501"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-only", action="store_true")
    parser.add_argument("--skip-daily", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    errors = environment_errors()
    if errors:
        for error in errors:
            print(f"ОШИБКА | {error}")
        return 2
    print(f"Окружение готово: Python {sys.version_info.major}.{sys.version_info.minor}")
    daily_ok = True if args.skip_daily else run_quick_daily()
    if args.daily_only:
        return 0 if daily_ok else 1
    ok, message = start_dashboard()
    print(message)
    if not ok:
        return 2
    if not args.no_browser:
        webbrowser.open("http://localhost:8501")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
