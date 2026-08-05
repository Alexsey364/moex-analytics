"""Historical 250-session validation without future reports."""

import pandas as pd


def metrics(frame: pd.DataFrame) -> dict:
    required = {"current_price", "actual_price", "median_price", "lower_price", "upper_price"}
    if frame.empty or not required.issubset(frame.columns):
        return {"n": 0, "status": "insufficient_data"}
    clean = frame.dropna(subset=list(required))
    if clean.empty:
        return {"n": 0, "status": "insufficient_data"}
    error = clean.median_price - clean.actual_price
    actual_return = clean.actual_price / clean.current_price - 1
    forecast_return = clean.median_price / clean.current_price - 1
    return {
        "n": len(clean),
        "mae": float(error.abs().mean()),
        "mape": float((error.abs() / clean.actual_price).mean()),
        "return_error": float((forecast_return - actual_return).abs().mean()),
        "sign_accuracy": float(((forecast_return >= 0) == (actual_return >= 0)).mean()),
        "interval_coverage": float(
            ((clean.actual_price >= clean.lower_price) & (clean.actual_price <= clean.upper_price)).mean()
        ),
        "average_width": float((clean.upper_price - clean.lower_price).mean()),
        "status": "descriptive_only" if len(clean) < 8 else "evaluated",
    }


def price_after_sessions(prices: pd.DataFrame, release_date, sessions: int = 250) -> float | None:
    future = prices[prices.trade_date > release_date].sort_values("trade_date")
    return None if len(future) < sessions else float(future.iloc[sessions - 1].close)


def validate_all(con) -> dict:
    frame = con.execute("""SELECT r.as_of_date,p.close current_price,
      lead(p.close,250) OVER(ORDER BY p.trade_date) actual_price,
      median(r.fair_value) median_price,min(r.lower_price) lower_price,max(r.upper_price) upper_price
      FROM valuation_results r JOIN canonical_daily_prices p ON p.trade_date=r.as_of_date
      WHERE r.secid='SBER' AND p.canonical_secid='SBER'
      GROUP BY r.as_of_date,p.trade_date,p.close ORDER BY r.as_of_date""").df()
    result = metrics(frame)
    result["note"] = (
        "One or two releases are not evidence; holdout since 2024 requires enough release vintages."
    )
    return result
