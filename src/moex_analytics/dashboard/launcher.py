"""Safe local dashboard port inspection."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

MARKER = Path(tempfile.gettempdir()) / "moex-analytics-dashboard-8501.json"


def mark_current_process(path: Path = MARKER) -> None:
    path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")


def _mark_pending(path: Path = MARKER) -> None:
    path.write_text(json.dumps({"pending_since": time.time()}), encoding="utf-8")


def _marker_pid(path: Path = MARKER) -> int | None:
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["pid"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _pending_is_fresh(path: Path = MARKER) -> bool:
    try:
        started = float(json.loads(path.read_text(encoding="utf-8"))["pending_since"])
        return 0 <= time.time() - started < 600
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _healthy(port: int = 8501) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=2) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except OSError:
        return False


def port_owner(port: int = 8501) -> tuple[int, str] | None:
    sockets = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, check=False)
    pid = None
    for line in sockets.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0] == "TCP" and fields[3] == "LISTENING":
            if fields[1].rsplit(":", 1)[-1] == str(port):
                pid = int(fields[4])
                break
    if pid is None:
        return None
    script = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
    process = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return pid, process.stdout.strip()


def classify_owner(owner: tuple[int, str] | None) -> str:
    if owner is None:
        return "free"
    if (owner[0] == _marker_pid() or _pending_is_fresh()) and _healthy():
        return "dashboard"
    return "other"


def main() -> int:
    owner = port_owner()
    status = classify_owner(owner)
    if status == "free":
        _mark_pending()
        return 3
    if status == "dashboard":
        print("Dashboard уже работает: http://localhost:8501")
        return 0
    print(f"[ERROR] Порт 8501 занят другим процессом. PID: {owner[0]}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
