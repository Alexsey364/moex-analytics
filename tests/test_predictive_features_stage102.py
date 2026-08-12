from __future__ import annotations

import duckdb
import pandas as pd

from moex_analytics.predictive_features import build_feature_store


def _db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
                "total_return_index DOUBLE,calculation_version VARCHAR)")
    dates = pd.bdate_range("2020-01-01", periods=300)
    rows = []
    for ticker, drift in (("AAA", .001), ("BBB", -.0002), ("IMOEX", .0003)):
        value = 100.0
        for i, day in enumerate(dates):
            value *= 1 + drift + (i % 11 - 5) * .0001
            rows.append((day, ticker, value, "frozen-v1"))
    con.executemany("INSERT INTO daily_returns VALUES (?,?,?,?)", rows)
    return con


def test_feature_store_is_pit_safe_compact_ranked_and_idempotent() -> None:
    con = _db()
    result = build_feature_store(con)
    assert result["rows"] == 900
    assert result["features"] < 50 and result["families"] == 10
    assert con.execute("SELECT count(*) FROM predictive_feature_store WHERE "
                       "history_end<>trade_date OR CAST(available_at AS DATE)<>trade_date").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM predictive_feature_store WHERE "
                       "momentum_rank<0 OR momentum_rank>1").fetchone()[0] == 0
    assert build_feature_store(con)["cached"] is True


def test_missing_optional_families_are_explicit_not_synthetic() -> None:
    con = _db()
    build_feature_store(con)
    assert con.execute("SELECT bool_and(key_rate IS NULL AND dividend_yield IS NULL) "
                       "FROM predictive_feature_store").fetchone()[0]
    rows = con.execute("SELECT status,observations FROM predictive_feature_diagnostics "
                       "WHERE diagnostic_type='missingness' AND feature_a='key_rate'").fetchone()
    assert rows == ("unavailable", 0)
