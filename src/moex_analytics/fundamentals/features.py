"""Missing-data-safe fundamental calculations."""

from collections.abc import Sequence


def safe_divide(a: float | None, b: float | None) -> float | None:
    return None if a is None or b in (None, 0) else a / b


def ttm(values: Sequence[float | None]) -> float | None:
    return sum(values) if len(values) == 4 and all(x is not None for x in values) else None


def annualize(value: float | None, months: int) -> float | None:
    return None if value is None or months <= 0 else value * 12 / months


def eps(profit: float | None, shares: float | None) -> float | None:
    return safe_divide(profit, shares)


def bvps(equity: float | None, shares: float | None) -> float | None:
    return safe_divide(equity, shares)


def roe(profit: float | None, opening: float | None, closing: float | None) -> float | None:
    return None if opening is None or closing is None else safe_divide(profit, (opening + closing) / 2)


def payout(dividend: float | None, earnings: float | None) -> float | None:
    return safe_divide(dividend, earnings)


def growth(current: float | None, previous: float | None) -> float | None:
    return None if current is None or previous in (None, 0) else current / previous - 1
