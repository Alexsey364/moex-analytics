from datetime import date

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.conditional_similarity.core import (
    _episode_representatives,
    _total_distance,
    build_conditional_similarity,
    build_state_panel,
    family_distances,
    similarity_score,
)


def test_similarity_is_monotonic_and_family_weights_renormalize():
    distances = pd.DataFrame(
        {"price": [0.2, 0.4], "market": [np.nan, 0.4]},
        index=pd.date_range("2020-01-01", periods=2),
    )
    total = _total_distance(distances, {"price": 0.25, "market": 0.75})
    assert total.iloc[0] == 0.2
    assert total.iloc[1] == 0.4
    assert similarity_score(total.iloc[0]) > similarity_score(total.iloc[1])
    assert 0 <= similarity_score(100) < similarity_score(0) == 100


def test_missing_family_is_not_imputed_as_zero():
    index = pd.date_range("2020-01-01", periods=30)
    history = pd.DataFrame({"price": np.arange(30.0), "missing": np.nan}, index=index)
    current = pd.Series({"price": 31.0, "missing": np.nan})
    result = family_distances(history, current, {"price": ("price",), "rates": ("missing",)})
    assert result.price.notna().all()
    assert result.rates.isna().all()


def test_neighboring_dates_form_one_deterministic_episode():
    index = pd.bdate_range("2021-12-20", periods=30)
    ranked = pd.DataFrame({"total_distance": np.linspace(0.1, 1, 30)}, index=index)
    ranked["date_tiebreak"] = ranked.index
    first = _episode_representatives(ranked, separation=10)
    second = _episode_representatives(ranked, separation=10)
    assert first == second
    assert first[index[1]] == index[0]
    assert len(set(first.values())) == 3


def _database() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """CREATE TABLE canonical_daily_prices(
        trade_date DATE,canonical_secid VARCHAR,close DOUBLE,high DOUBLE,low DOUBLE)"""
    )
    dates = pd.bdate_range("2018-01-01", periods=820)
    values = 100 * np.exp(np.linspace(0, 0.5, len(dates)) + np.sin(np.arange(len(dates)) / 20) * 0.04)
    con.executemany(
        "INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?)",
        [
            (day.date(), "SBERP", value, value * 1.01, value * 0.99)
            for day, value in zip(dates, values, strict=True)
        ],
    )
    con.execute(
        """CREATE TABLE whole_market_state_runs(
        run_id VARCHAR,cutoff DATE,status VARCHAR)"""
    )
    con.execute("INSERT INTO whole_market_state_runs VALUES ('market',?,'completed')", [dates[-1].date()])
    con.execute(
        """CREATE TABLE whole_market_state_daily(
        run_id VARCHAR,trade_date DATE,return_5 DOUBLE,return_20 DOUBLE,return_60 DOUBLE,
        drawdown DOUBLE,realized_vol20 DOUBLE,market_state_label VARCHAR)"""
    )
    market = pd.Series(values, index=dates)
    peak = market.cummax()
    rows = []
    def ret(position, window):
        if position < window:
            return None
        return float(market.iloc[position] / market.iloc[position - window] - 1)

    for position, day in enumerate(dates):
        rows.append(
            (
                "market",
                day.date(),
                ret(position, 5),
                ret(position, 20),
                ret(position, 60),
                float(market.iloc[position] / peak.iloc[position] - 1),
                0.2,
                "range",
            )
        )
    con.executemany("INSERT INTO whole_market_state_daily VALUES (?,?,?,?,?,?,?,?)", rows)
    return con


def test_stage95_run_is_pit_immutable_and_idempotent():
    con = _database()
    cutoff = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    panel, _ = build_state_panel(con, "SBERP", cutoff)
    assert panel.index.max().date() == cutoff
    assert build_state_panel(con, "SBERP", date(2020, 1, 1))[0].index.max().date() <= date(2020, 1, 1)
    result = build_conditional_similarity(con)
    assert result["status"] == "completed" and result["candidates"] > 0
    assert build_conditional_similarity(con)["idempotent"]
    assert con.execute(
        """SELECT count(*) FROM conditional_analog_diagnostics
        WHERE analog_date>history_end OR immutable IS DISTINCT FROM TRUE"""
    ).fetchone()[0] == 0
    assert con.execute(
        """SELECT bool_and(production_unchanged AND probability_gate_unchanged)
        FROM conditional_similarity_runs"""
    ).fetchone()[0]
