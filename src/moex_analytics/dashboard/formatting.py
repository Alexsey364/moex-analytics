"""Russian display formatting."""

from __future__ import annotations

from datetime import date, datetime


def format_date(value: date | datetime | None) -> str:
    return "—" if value is None else value.strftime("%d.%m.%Y")


def format_number(value: float | int | None, decimals: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def format_percent(value: float | None, decimals: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{decimals}f}%".replace(".", ",")
