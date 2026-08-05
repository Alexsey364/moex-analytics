"""Safe point-in-time transformations for interim reports."""

from __future__ import annotations


def ytd_to_period(
    current: float,
    previous: float | None,
    *,
    comparable: bool,
    current_months: int,
    previous_months: int,
    revised: bool = False,
) -> dict:
    if not comparable or revised or previous is None or current_months <= previous_months:
        return {"value": None, "status": "not_comparable", "formula": None}
    return {
        "value": current - previous,
        "status": "derived",
        "formula": "current_ytd - comparable_previous_ytd",
    }


def annualize(value: float, months: int) -> float | None:
    return value * 12 / months if 0 < months <= 12 else None
