import duckdb
import numpy as np
import pandas as pd

import moex_analytics.predictive_expansion.market as market
from moex_analytics.actual_backfill.schema import DDL as ACTUAL_DDL


def test_stage30_schema_is_research_only_and_has_required_families():
    con = duckdb.connect(":memory:")
    market.ensure_schema(con)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    assert "stage30_liquidity_daily" in tables
    assert "stage30_breadth_daily" in tables
    assert "stage30_security_quality" in tables
    assert "stage30_data_value_ledger" in tables
    assert "production" not in " ".join(tables)


def test_empty_stage30_market_status():
    con = duckdb.connect(":memory:")
    assert market.expansion_market_status(con) == {"latest": None, "tiers": []}


def test_horizons_are_frozen_before_research():
    assert market.HORIZONS == (5, 20, 60, 120)


def test_market_feature_build_is_pit_research_only(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute(ACTUAL_DDL)
    con.execute(
        """CREATE TABLE issuer_fundamental_values(
        secid VARCHAR,period_end DATE,validation_status VARCHAR)"""
    )
    dates = pd.bdate_range("2020-01-01", periods=320)
    rng = np.random.default_rng(30)
    rows = []
    for secid in ("AAA", "BBB"):
        close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, len(dates)))
        for index, (tradedate, price) in enumerate(zip(dates, close, strict=True)):
            rows.append(
                (
                    tradedate.date(),
                    secid,
                    "TQBR",
                    0,
                    price,
                    price * 1.01,
                    price * 0.99,
                    price,
                    price,
                    price,
                    price,
                    price,
                    price,
                    1_000_000 + index,
                    10_000 + index,
                    100 + index,
                    "SUR",
                    "official",
                    "hash",
                    pd.Timestamp.now(),
                )
            )
    con.executemany("INSERT INTO moex_equity_eod VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(market, "quality_audit", lambda _con: {"corporate_action_flags": 0})
    monkeypatch.setattr(market, "survivorship_diagnostic", lambda _con: {"status": "test"})
    result = market.build_market_features(con)
    assert result["production_changes"] == 0
    assert result["securities"] == 2
    assert result["liquidity_rows"] == 640
    assert result["breadth_days"] == 320
    status = market.expansion_market_status(con)
    assert status["latest"][1] == "completed"


def test_survivorship_checkpoint_is_thresholded_and_idempotent(monkeypatch):
    con = duckdb.connect(":memory:")
    market.ensure_schema(con)
    monkeypatch.setattr(
        market,
        "survivorship_diagnostic",
        lambda _con: {
            "days": 100,
            "mean_difference": 0.01,
            "median_difference": 0.0,
            "p95_absolute_difference": 0.05,
        },
    )
    market._capture_survivorship(con, 1000)
    market._capture_survivorship(con, 1000)
    assert con.execute("SELECT count(*) FROM stage30_survivorship_diagnostics").fetchone()[0] == 2
