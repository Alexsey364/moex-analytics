from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from moex_analytics.predictive_targets import core
from moex_analytics.predictive_targets.core import (
    PATH_SHAPES,
    _path_shape,
    build_predictive_targets,
    ensure_schema,
    target_status,
)


def _database() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
                "total_return_index DOUBLE,calculation_version VARCHAR)")
    dates = pd.bdate_range("2020-01-01", periods=270)
    rows = []
    for secid, drift in (("AAA", .001), ("BBB", -.0002), ("IMOEX", .0003)):
        value = 100.0
        for number, date in enumerate(dates):
            value *= 1 + drift + (number % 7 - 3) * .0001
            rows.append([date, secid, value, "actual-dividends-v1"])
    con.executemany("INSERT INTO daily_returns VALUES (?,?,?,?)", rows)
    return con


def test_target_dataset_is_immutable_ranked_and_temporally_matured() -> None:
    con = _database()
    result = build_predictive_targets(con)
    assert result["status"] == "completed"
    assert result["observations"] > 0
    assert con.execute("SELECT bool_and(immutable),count(distinct horizon) "
                       "FROM predictive_target_observations").fetchone() == (True, 6)
    assert con.execute("SELECT count(*) FROM predictive_target_observations "
                       "WHERE exit_date<=trade_date OR history_end<>trade_date").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM predictive_target_observations "
                       "WHERE percentile_rank<0 OR percentile_rank>1").fetchone()[0] == 0
    assert con.execute("SELECT distinct sector_status FROM predictive_target_observations").fetchall() == [
        ("unavailable_no_pit_sector_mapping",)
    ]
    assert build_predictive_targets(con)["cached"] is True
    assert con.execute("SELECT count(*) FROM predictive_target_runs").fetchone()[0] == 1


def test_entry_policies_never_select_future_minimum() -> None:
    con = _database()
    build_predictive_targets(con)
    policies = {
        row[0]
        for row in con.execute("SELECT DISTINCT policy FROM predictive_entry_targets").fetchall()
    }
    assert policies == {
        "BUY_NOW", "WAIT_3", "WAIT_5", "WAIT_10", "BUY_AFTER_DIP_2", "BUY_AFTER_DIP_3"
    }
    assert con.execute("SELECT count(*) FROM predictive_entry_targets "
                       "WHERE entry_date<trade_date OR entry_date>exit_date").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM predictive_entry_targets "
                       "WHERE policy LIKE 'BUY_AFTER_DIP%' AND entered "
                       "AND (entry_price>signal_threshold OR entry_date IS NULL)").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM predictive_target_observations "
                       "WHERE secid='IMOEX' AND percentile_rank IS NOT NULL").fetchone()[0] == 0


def test_path_classes_are_fixed_and_known() -> None:
    cases = ([.01, .02, .04], [-.01, -.02, -.04], [-.06, -.01, .03], [.06, .02, -.01],
             [.001, -.001, .002], [.08, -.08, .12])
    assert {_path_shape(pd.Series(case).to_numpy()) for case in cases} <= PATH_SHAPES
    assert _path_shape(pd.Series([-.01, -.02, -.03, -.04, -.05, -.03, -.01, .02]).to_numpy()) == (
        "dip_then_recover"
    )
    assert _path_shape(pd.Series([.01, .02, .03, .04, .05, .03, .01, -.005]).to_numpy()) == (
        "rise_then_fall"
    )


def test_schema_has_explicit_named_immutable_contract() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    columns = {row[0] for row in con.execute("DESCRIBE predictive_target_observations").fetchall()}
    assert {"total_return", "percentile_rank", "mfe", "mae", "path_shape", "immutable"} <= columns
    assert target_status(con) == {"latest": None}


def test_source_contract_and_failed_run_are_auditable(monkeypatch: pytest.MonkeyPatch) -> None:
    con = _database()
    con.execute("INSERT INTO daily_returns VALUES ('2019-01-01','IMOEX',99,'other-version')")
    with pytest.raises(ValueError, match="exactly one"):
        build_predictive_targets(con)

    con = _database()
    monkeypatch.setattr(core, "_records", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        build_predictive_targets(con)
    assert con.execute("SELECT status FROM predictive_target_runs").fetchone()[0] == "failed"
    assert target_status(con)["status"] == "failed"
