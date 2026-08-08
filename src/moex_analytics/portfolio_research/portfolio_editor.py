"""Validated private portfolio editing with backup and atomic persistence."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from moex_analytics.config import PROJECT_ROOT

PORTFOLIO_PATH = PROJECT_ROOT / "config/portfolio_positions.local.yaml"
BACKUP_DIR = PROJECT_ROOT / "data/local/portfolio_backups"
SECID_PATTERN = re.compile(r"^[A-Z0-9]{1,12}$")
EDITABLE_FIELDS = ("secid", "quantity", "average_price", "allow_buy", "allow_sell", "frozen", "notes")


def load_positions(path: Path = PORTFOLIO_PATH) -> list[dict]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return [{key: item.get(key) for key in EDITABLE_FIELDS} for item in (cfg or {}).get("positions", [])]


def instrument_registry(con) -> dict[str, str]:
    rows = con.execute("SELECT secid,coalesce(name,secid) FROM instruments").fetchall()
    try:
        rows += con.execute(
            "SELECT secid,coalesce(name,secid) FROM portfolio_instruments"
        ).fetchall()
    except Exception:
        pass
    return dict(rows)


def validate_positions(rows: list[dict], known: set[str]) -> list[dict]:
    result, seen = [], set()
    for number, row in enumerate(rows, 1):
        secid = str(row.get("secid", "")).strip().upper()
        if not secid:
            continue
        if not SECID_PATTERN.fullmatch(secid):
            raise ValueError(f"Строка {number}: некорректный SECID {secid}")
        if secid not in known:
            raise ValueError(f"SECID {secid} не найден; выполните официальный MOEX discovery")
        if secid in seen:
            raise ValueError(f"SECID {secid} указан дважды")
        try:
            quantity = float(row["quantity"])
            average_price = float(row["average_price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Строка {number}: количество и средняя цена обязательны") from exc
        if quantity <= 0 or average_price < 0:
            raise ValueError("Количество должно быть положительным, средняя цена — неотрицательной")
        seen.add(secid)
        result.append(
            {
                "secid": secid,
                "quantity": quantity,
                "average_price": average_price,
                "target_weight": None,
                "maximum_weight": None,
                "allow_buy": bool(row.get("allow_buy", True)),
                "allow_sell": bool(row.get("allow_sell", True)),
                "frozen": bool(row.get("frozen", False)),
                "notes": str(row.get("notes") or ""),
            }
        )
    return result


def position_diff(before: list[dict], after: list[dict]) -> list[str]:
    old, new = {row["secid"]: row for row in before}, {row["secid"]: row for row in after}
    changes = []
    for secid in sorted(old.keys() | new.keys()):
        if secid not in old:
            changes.append(f"Добавится: {secid} {new[secid]['quantity']:g}")
        elif secid not in new:
            changes.append(f"Удалится: {secid} {old[secid]['quantity']:g}")
        elif any(old[secid].get(key) != new[secid].get(key) for key in EDITABLE_FIELDS):
            changes.append(
                f"Было: {secid} {old[secid]['quantity']:g} → "
                f"Станет: {secid} {new[secid]['quantity']:g}"
            )
    return changes


def save_positions(
    rows: list[dict],
    known: set[str],
    path: Path = PORTFOLIO_PATH,
    backup_dir: Path = BACKUP_DIR,
) -> Path | None:
    positions = validate_positions(rows, known)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg, backup = cfg or {}, None
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"portfolio_{datetime.now():%Y%m%d_%H%M%S_%f}.yaml"
        shutil.copy2(path, backup)
    cfg.update({"mode": "real", "positions": positions})
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".portfolio.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(cfg, stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def recalculate_portfolio(con):
    """Recalculate derived portfolio state without redownloading market history."""
    from .human_intelligence import run_daily_intelligence
    from .portfolio_v14 import calculate_real_portfolio

    portfolio = calculate_real_portfolio(con)
    report = run_daily_intelligence(con, update_data=False)
    return {"portfolio": portfolio, "report": report}
