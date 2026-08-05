"""Explainable bank valuation formulas."""

from .features import safe_divide


def pe(price: float | None, trailing_eps: float | None) -> float | None:
    return safe_divide(price, trailing_eps)


def pb(price: float | None, book_value_per_share: float | None) -> float | None:
    return safe_divide(price, book_value_per_share)


def justified_pb(roe: float, growth: float, cost_of_equity: float) -> float | None:
    if cost_of_equity <= growth:
        return None
    return (roe - growth) / (cost_of_equity - growth)


def pe_scenario(profit: float, shares: float, target_pe: float) -> float:
    return profit / shares * target_pe


def pb_roe_scenario(
    book_value_per_share: float,
    roe_value: float,
    growth: float,
    cost_of_equity: float,
    target_pb: float | None = None,
) -> float | None:
    multiple = target_pb if target_pb is not None else justified_pb(roe_value, growth, cost_of_equity)
    return None if multiple is None else book_value_per_share * multiple


def dividend_discount(dividend: float, cost_of_equity: float, growth: float) -> float | None:
    return None if cost_of_equity <= growth else dividend * (1 + growth) / (cost_of_equity - growth)


def margin_of_safety(
    price: float | None,
    base: float | None,
    conservative: float | None,
    optimistic: float | None,
    stress: float | None,
) -> dict[str, float | str | None]:
    discounts = {
        "base_discount": None if not price or base is None else base / price - 1,
        "conservative_discount": None if not price or conservative is None else conservative / price - 1,
        "optimistic_upside": None if not price or optimistic is None else optimistic / price - 1,
        "stress_downside": None if not price or stress is None else stress / price - 1,
    }
    d = discounts["base_discount"]
    discounts["status"] = (
        "недостаточно данных"
        if d is None
        else "значительный запас прочности"
        if d >= 0.3
        else "умеренный запас прочности"
        if d >= 0.1
        else "оценка близка к справедливой"
        if d >= -0.1
        else "умеренная переоценка"
        if d >= -0.3
        else "значительная переоценка"
    )
    return discounts
