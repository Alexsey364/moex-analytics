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

from moex_analytics import update_monitor
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


def configure_console() -> None:
    """Keep Russian launcher diagnostics readable under cmd.exe code page 65001."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def environment_errors() -> list[str]:
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append(f"Требуется Python 3.12, выбран {sys.version_info.major}.{sys.version_info.minor}.")
    missing = [name for name in REQUIRED_IMPORTS if importlib.util.find_spec(name) is None]
    if missing:
        errors.append("Отсутствуют обязательные библиотеки: " + ", ".join(missing))
    return errors


def run_quick_daily() -> bool:
    previous = update_monitor.recover_interrupted()
    if previous.get("status") in {"starting", "running", "waiting_source", "retrying"} and (
        update_monitor.process_alive(previous.get("pid"))
    ):
        print("Обновление уже выполняется. Открываю dashboard/status page.")
        return True
    print("Обновление последних данных... Ctrl+C = остановить после текущего запроса", flush=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "moex_analytics.cli", "quick-daily-update"],
        cwd=PROJECT_ROOT,
    )
    last_line = None
    try:
        while process.poll() is None:
            state = update_monitor.load()
            if state:
                health = update_monitor.health(state)
                eta = update_monitor.eta_seconds(state)
                line = (f"[{state.get('items_done', 0)}/{state.get('items_total', '?')}] "
                        f"{state.get('current_source') or 'starting'} | "
                        f"{state.get('current_stage')} | rows +{state.get('rows_inserted', 0)} | "
                        f"requests {state.get('requests_completed', 0)} | {health} | "
                        f"ETA {int(eta)}s" if eta is not None else
                        f"[{state.get('items_done', 0)}/{state.get('items_total', '?')}] "
                        f"{state.get('current_source') or 'starting'} | "
                        f"{state.get('current_stage')} | rows +{state.get('rows_inserted', 0)} | "
                        f"requests {state.get('requests_completed', 0)} | {health} | ETA unavailable")
                if line != last_line:
                    print(line, flush=True)
                    last_line = line
            time.sleep(1)
    except KeyboardInterrupt:
        print("Запрошена безопасная остановка после текущего шага...")
        update_monitor.request_cancel()
        process.wait()
    result_code = process.wait()
    state = update_monitor.load()
    print(f"ГОТОВО | status={state.get('status')} | requests={state.get('requests_completed', 0)} | "
          f"rows={state.get('rows_inserted', 0)} | errors={state.get('errors', 0)}")
    if result_code:
        print("Предупреждение: обновление не завершено; dashboard покажет последние сохранённые данные.")
    return result_code == 0


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
    started_owner = port_launcher.port_owner()
    if started_owner is not None:
        port_launcher.mark_process(started_owner[0])
    return True, "Dashboard запущен: http://localhost:8501"


def main(argv: list[str] | None = None) -> int:
    configure_console()
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
