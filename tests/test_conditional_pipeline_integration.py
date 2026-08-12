import duckdb
import numpy as np
import pandas as pd

from moex_analytics.barrier_analytics import build_barrier_analytics
from moex_analytics.conditional_forecast import build_conditional_forecasts
from moex_analytics.conditional_paths import build_conditional_paths
from moex_analytics.conditional_similarity import build_conditional_similarity
from moex_analytics.conditional_validation import build_conditional_validation
from moex_analytics.regime_conditioning import build_regime_conditioning


def _research_database() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """CREATE TABLE canonical_daily_prices(
        trade_date DATE,canonical_secid VARCHAR,close DOUBLE,high DOUBLE,low DOUBLE)"""
    )
    dates = pd.bdate_range("2018-01-01", periods=820)
    phase = np.arange(len(dates), dtype=float)
    values = 100 * np.exp(phase / 3000 + np.sin(phase / 18) * 0.05 + np.sin(phase / 70) * 0.03)
    con.executemany(
        "INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?)",
        [
            (day.date(), "SBERP", value, value * 1.01, value * 0.99)
            for day, value in zip(dates, values, strict=True)
        ],
    )
    con.execute("CREATE TABLE whole_market_state_runs(run_id VARCHAR,cutoff DATE,status VARCHAR)")
    con.execute("INSERT INTO whole_market_state_runs VALUES ('market',?,'completed')", [dates[-1].date()])
    con.execute(
        """CREATE TABLE whole_market_state_daily(
        run_id VARCHAR,trade_date DATE,return_5 DOUBLE,return_20 DOUBLE,return_60 DOUBLE,
        drawdown DOUBLE,realized_vol20 DOUBLE,market_state_label VARCHAR)"""
    )
    market = pd.Series(values, index=dates)
    peak = market.cummax()

    def trailing(position: int, window: int) -> float | None:
        if position < window:
            return None
        return float(market.iloc[position] / market.iloc[position - window] - 1)

    rows = [
        (
            "market",
            day.date(),
            trailing(position, 5),
            trailing(position, 20),
            trailing(position, 60),
            float(market.iloc[position] / peak.iloc[position] - 1),
            float(0.18 + 0.04 * abs(np.sin(position / 40))),
            "transition_or_range",
        )
        for position, day in enumerate(dates)
    ]
    con.executemany("INSERT INTO whole_market_state_daily VALUES (?,?,?,?,?,?,?,?)", rows)
    return con


def test_full_conditional_research_pipeline_is_reproducible_and_gated():
    con = _research_database()
    similarity = build_conditional_similarity(con)
    assert similarity["candidates"] > 0 and similarity["accepted"] > 0
    forecast = build_conditional_forecasts(con)
    assert forecast["forecasts"] == 9
    regime = build_regime_conditioning(con)
    assert regime["timeline_rows"] == 820 and regime["analog_rows"] > 0
    paths = build_conditional_paths(con)
    assert paths["curve_rows"] == 251
    barriers = build_barrier_analytics(con)
    assert barriers["rows"] == 378
    validation = build_conditional_validation(con)
    assert validation["replay_rows"] > 0 and validation["scorecards"] > 0
    assert build_conditional_validation(con)["idempotent"]
    assert con.execute(
        "SELECT count(*) FROM conditional_replay_forecasts WHERE history_end>=evaluation_date"
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT bool_and(NOT probability_published) FROM conditional_barrier_results"
    ).fetchone()[0]
    assert con.execute(
        "SELECT bool_and(production_unchanged AND probability_gate_unchanged) "
        "FROM conditional_validation_runs"
    ).fetchone()[0]
