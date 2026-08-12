from __future__ import annotations

import duckdb
import pandas as pd

from moex_analytics.baseline_models import build_baseline_suite, ensure_schema
from moex_analytics.predictive_targets.core import build_predictive_targets


def _database() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
                "total_return_index DOUBLE,calculation_version VARCHAR)")
    dates = pd.bdate_range("2018-01-01", periods=330)
    rows = []
    for secid, drift in (("AAA", .0004), ("BBB", -.0001), ("IMOEX", .0002)):
        value = 100.0
        for number, date in enumerate(dates):
            value *= 1 + drift + ((number % 13) - 6) * .0002
            rows.append((date, secid, value, "actual-dividends-v1"))
    con.executemany("INSERT INTO daily_returns VALUES (?,?,?,?)", rows)
    return con


def test_supervised_targets_have_explicit_timestamps_and_no_future_features() -> None:
    con = _database()
    build_predictive_targets(con)
    columns = {row[0] for row in con.execute("DESCRIBE predictive_return_targets").fetchall()}
    assert {"forward_return", "market_return", "up", "target_available_date",
            "feature_timestamp", "evaluation_timestamp", "max_drawdown"} <= columns
    assert con.execute("SELECT count(*) FROM predictive_return_targets WHERE "
                       "history_end<>evaluation_date OR target_available_date<=evaluation_date OR "
                       "feature_timestamp<>evaluation_timestamp").fetchone()[0] == 0
    row = con.execute("SELECT forward_return,forward_log_return,up FROM predictive_return_targets "
                      "WHERE secid='AAA' ORDER BY evaluation_date,horizon LIMIT 1").fetchone()
    assert abs(row[1] - __import__("math").log1p(row[0])) < 1e-12
    assert row[2] == (row[0] > 0)


def test_baselines_are_pit_safe_ranked_and_idempotent() -> None:
    con = _database()
    build_predictive_targets(con)
    result = build_baseline_suite(con)
    assert result["predictions"] > 0 and result["scorecards"] > 0
    invalid = con.execute("SELECT count(*) FROM predictive_baseline_predictions WHERE "
                          "training_end>evaluation_date OR "
                          "target_available_date<=evaluation_date").fetchone()[0]
    assert invalid == 0
    assert con.execute("SELECT count(*) FROM predictive_baseline_scorecards WHERE rank=1").fetchone()[0] > 0
    assert con.execute("SELECT bool_and(immutable) FROM predictive_baseline_predictions").fetchone()[0]
    assert build_baseline_suite(con)["cached"] is True
    assert con.execute("SELECT count(*) FROM predictive_baseline_runs").fetchone()[0] == 1


def test_unavailable_valuation_baseline_is_not_synthetic_filled() -> None:
    con = _database()
    build_predictive_targets(con)
    build_baseline_suite(con)
    models = {row[0] for row in con.execute(
        "SELECT DISTINCT model FROM predictive_baseline_predictions").fetchall()}
    assert "no_change" in models
    assert "simple_valuation" not in models
    ensure_schema(con)
