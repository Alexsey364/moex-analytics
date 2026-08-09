"""Dependency preflight that works before moex-analytics itself is installed."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Dependency:
    distribution: str
    import_name: str
    display_name: str


REQUIRED = (
    Dependency("duckdb", "duckdb", "DuckDB"),
    Dependency("streamlit", "streamlit", "Streamlit"),
    Dependency("pandas", "pandas", "pandas"),
    Dependency("numpy", "numpy", "numpy"),
    Dependency("scikit-learn", "sklearn", "sklearn"),
    Dependency("plotly", "plotly", "plotly"),
    Dependency("pyarrow", "pyarrow", "pyarrow"),
    Dependency("scipy", "scipy", "scipy"),
    Dependency("joblib", "joblib", "joblib"),
    Dependency("requests", "requests", "requests"),
    Dependency("PyYAML", "yaml", "PyYAML"),
    Dependency("openpyxl", "openpyxl", "openpyxl"),
)
MIN_PYTHON = (3, 12)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def missing_dependencies() -> list[Dependency]:
    return [item for item in REQUIRED if importlib.util.find_spec(item.import_name) is None]


def print_environment(missing: list[Dependency]) -> None:
    print("Проверка окружения...", flush=True)
    python_ok = tuple(sys.version_info[:2]) >= MIN_PYTHON
    print(f"{'✔' if python_ok else '✘'} Python {sys.version_info.major}.{sys.version_info.minor}")
    missing_names = {item.import_name for item in missing}
    for item in REQUIRED:
        mark = "✘" if item.import_name in missing_names else "✔"
        print(f"{mark} {item.display_name}")


def install_project(project_root: Path) -> bool:
    command = [sys.executable, "-m", "pip", "install", "-e", "."]
    print(f"\nУстановка: {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=project_root, check=False)
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="install missing packages without prompting")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    missing = missing_dependencies()
    print_environment(missing)
    if tuple(sys.version_info[:2]) < MIN_PYTHON:
        print("\n[ОШИБКА] Требуется Python 3.12 или новее.")
        return 2
    if not missing:
        print("\nВсе зависимости установлены.\nЗапуск аналитики...", flush=True)
        return 0

    names = ", ".join(item.distribution for item in missing)
    print(f"\nНе найдены обязательные пакеты: {names}.")
    try:
        answer = "yes" if args.yes else input("Установить автоматически? [Y/N]: ")
    except EOFError:
        answer = ""
    consent = answer.strip().lower() in {"y", "yes", "д", "да"}
    if not consent:
        print("Установка отменена. Dashboard не запущен.")
        return 1
    if not install_project(args.project_root.resolve()):
        print("[ОШИБКА] Автоматическая установка завершилась с ошибкой.")
        return 1

    importlib.invalidate_caches()
    still_missing = missing_dependencies()
    if still_missing:
        names = ", ".join(item.distribution for item in still_missing)
        print(f"[ОШИБКА] После установки всё ещё отсутствуют: {names}.")
        return 1
    print("\nВсе зависимости установлены.\nЗапуск аналитики...", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
