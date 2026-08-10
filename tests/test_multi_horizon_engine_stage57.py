from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.multi_horizon_engine import core
from moex_analytics.multi_horizon_engine.core import (
    ensure_schema,
    expert_for_horizon,
    interpretation,
    multi_horizon_status,
)


def test_horizon_gate_is_deterministic_and_not_outcome_based() -> None:
    assert expert_for_horizon(5) == "short_horizon_expert"
    assert expert_for_horizon(20) == "short_horizon_expert"
    assert expert_for_horizon(60) == "medium_horizon_expert"
    assert expert_for_horizon(120) == "long_horizon_expert"
    assert expert_for_horizon(250) == "long_horizon_expert"


def test_cross_horizon_difference_is_not_called_contradiction() -> None:
    assert interpretation(-0.02, 0.10) == "long_term_interesting_short_term_timing_weak"
    assert interpretation(0.02, -0.10) == "short_term_strength_long_term_risk"
    assert interpretation(None, 0.10) == "insufficient_data"


def test_schema_preserves_ablation_and_term_structure() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert multi_horizon_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE horizon_feature_ablation").fetchall()}
    assert {"validation_contribution", "holdout_contribution", "gate_status", "immutable"} <= columns


def test_full_multi_horizon_run_uses_validation_gate_and_frozen_holdout(monkeypatch) -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE ranking_research_runs(run_id VARCHAR,target_run_id VARCHAR,"
        "cutoff DATE,train_end DATE,validation_end DATE,holdout_start DATE,status VARCHAR,"
        "finished_at TIMESTAMP)"
    )
    con.execute("CREATE TABLE opportunity_research_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute(
        "CREATE TABLE predictive_target_observations(run_id VARCHAR,trade_date DATE,"
        "exit_date DATE,secid VARCHAR,horizon INTEGER,percentile_rank DOUBLE)"
    )
    con.execute(
        "CREATE TABLE opportunity_candidates(run_id VARCHAR,candidate_type VARCHAR,"
        "secid VARCHAR,horizon INTEGER,expected_median DOUBLE,downside_axis DOUBLE,"
        "relative_rank DOUBLE,evidence_quality VARCHAR,timing_status VARCHAR,"
        "abstain BOOLEAN)"
    )
    dates = pd.bdate_range("2018-01-01", periods=320)
    con.execute(
        "INSERT INTO ranking_research_runs VALUES ('rank','target',?,?,?,?, 'completed',current_timestamp)",
        [dates[-1], dates[179], dates[249], dates[250]],
    )
    con.execute("INSERT INTO opportunity_research_runs VALUES ('opportunity','completed',current_timestamp)")
    rng = np.random.default_rng(4)
    feature_rows = []
    label_rows = []
    for date_number, date in enumerate(dates):
        for stock_number, secid in enumerate(("AAA", "BBB", "CCC")):
            values = rng.normal(size=len(core.FEATURES))
            feature_rows.append([date, secid, *values])
            for horizon in (5, 20):
                if date_number + horizon < len(dates):
                    rank = (stock_number + 1) / 3 + values[0] * 0.01
                    label_rows.append(["target", date, dates[date_number + horizon], secid, horizon, rank])
    features = pd.DataFrame(feature_rows, columns=("trade_date", "secid", *core.FEATURES))
    con.executemany("INSERT INTO predictive_target_observations VALUES (?,?,?,?,?,?)", label_rows)
    for secid, rank in (("AAA", 0.2), ("BBB", 0.5), ("CCC", 0.8)):
        for horizon in (5, 20):
            con.execute(
                "INSERT INTO opportunity_candidates VALUES "
                "('opportunity','equity',?,?,.02,.1,?,'research_oos',"
                "'buy_now_not_beaten',false)",
                [secid, horizon, rank],
            )
    monkeypatch.setattr(core, "_feature_panel", lambda _con: features)
    monkeypatch.setattr(core, "HORIZONS", (5, 20))
    result = core.run_multi_horizon_research(con)
    assert result["status"] == "completed"
    assert result["current_rows"] == 6
    assert (
        con.execute(
            "SELECT bool_and(selection_sample='validation_only') FROM horizon_expert_policies"
        ).fetchone()[0]
        is True
    )
    assert con.execute("SELECT bool_and(immutable) FROM horizon_feature_ablation").fetchone()[0] is True
    assert core.run_multi_horizon_research(con)["cached"] is True
