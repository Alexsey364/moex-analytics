from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from moex_analytics.whole_market_live.core import (
    MARKET_HORIZONS,
    RANK_HORIZONS,
    create_live_forecasts,
    evaluate_live_forecasts,
    live_forecast_status,
)
from moex_analytics.whole_market_live.schema import ensure_schema


def test_live_stream_contract_has_separate_horizons_and_no_probability() -> None:
    assert MARKET_HORIZONS == (1, 5, 20, 60, 120)
    assert RANK_HORIZONS == (5, 20, 60, 120, 250)


def test_real_eod_matures_once_and_preserves_probability_gate() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    cutoff = date(2026, 1, 5)
    con.executemany(
        "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
        [(cutoff + timedelta(days=index), "IMOEX", 100 + index) for index in range(3)],
    )
    now = datetime.now(UTC)
    con.execute(
        """INSERT INTO whole_market_live_runs VALUES
        ('run',?,?,1,0,0,'test',TRUE,FALSE,'completed','{}')""",
        [now, cutoff],
    )
    con.execute(
        """INSERT INTO live_market_forecasts VALUES
        ('forecast','run',?,?,'IMOEX',1,'up',0.005,-0.01,0.02,'test','test',
        TRUE,FALSE,'pending','hash')""",
        [now, cutoff],
    )
    assert evaluate_live_forecasts(con) == {"newly_matured": 1}
    assert evaluate_live_forecasts(con) == {"newly_matured": 0}
    outcome = con.execute(
        "SELECT actual_return,direction_correct,immutable FROM whole_market_live_outcomes"
    ).fetchone()
    assert outcome[0] == pytest.approx(0.01)
    assert outcome[1:] == (True, True)
    status = live_forecast_status(con)
    assert status["pending"] == 0
    assert status["probability_allowed"] is False


def test_live_status_before_any_run_is_explicit() -> None:
    con = duckdb.connect(":memory:")
    assert live_forecast_status(con) == {"status": "not_run"}


def test_full_live_capture_is_immutable_idempotent_and_ranked() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE whole_market_state_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute(
        """CREATE TABLE whole_market_state_daily(run_id VARCHAR,trade_date DATE,imoex_close DOUBLE,
        market_state_label VARCHAR,return_20 DOUBLE,realized_vol20 DOUBLE)"""
    )
    con.execute("INSERT INTO whole_market_state_runs VALUES ('state','2026-01-01')")
    start = date(2025, 1, 1)
    state_rows = [
        ("state", start + timedelta(days=index), 100 + index, "stress", 0.01, 0.2)
        for index in range(140)
    ]
    con.executemany("INSERT INTO whole_market_state_daily VALUES (?,?,?,?,?,?)", state_rows)
    con.execute(
        """CREATE TABLE macro_observations(observation_date DATE,series_id VARCHAR,value DOUBLE,
        available_from TIMESTAMP)"""
    )
    sectors = (
        "moex_chemicals",
        "moex_consumer",
        "moex_finance",
        "moex_metals",
        "moex_oil_gas",
        "moex_power",
        "moex_telecom",
        "moex_transport",
    )
    macro_rows = []
    for index in range(70):
        day = start + timedelta(days=index)
        macro_rows.extend((day, sector, 100 + index + offset, day) for offset, sector in enumerate(sectors))
    con.executemany("INSERT INTO macro_observations VALUES (?,?,?,?)", macro_rows)
    con.execute("CREATE TABLE predictive_fusion_runs(run_id VARCHAR,created_at TIMESTAMP,status VARCHAR)")
    con.execute(
        """CREATE TABLE current_fusion_research(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
        cutoff DATE,signal VARCHAR,predicted_return DOUBLE,status VARCHAR)"""
    )
    con.execute("INSERT INTO predictive_fusion_runs VALUES ('fusion','2026-01-01','completed')")
    fusion_rows = []
    stocks = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
    for horizon in RANK_HORIZONS:
        fusion_rows.extend(
            ("fusion", secid, horizon, start + timedelta(days=139), "positive", rank / 100, "shadow")
            for rank, secid in enumerate(stocks, start=1)
        )
    con.executemany("INSERT INTO current_fusion_research VALUES (?,?,?,?,?,?,?)", fusion_rows)
    first = create_live_forecasts(con)
    second = create_live_forecasts(con)
    assert (first["market"], first["sectors"], first["stocks"]) == (5, 40, 45)
    assert first["probability_allowed"] is False
    assert second["idempotent"] is True
    assert con.execute("SELECT count(*) FROM live_market_forecasts").fetchone()[0] == 5
