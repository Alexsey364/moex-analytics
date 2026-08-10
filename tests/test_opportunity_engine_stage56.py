from __future__ import annotations

import duckdb
import pandas as pd

from moex_analytics.opportunity_engine import core
from moex_analytics.opportunity_engine.core import (
    ensure_schema,
    opportunity_status,
    pareto_pairs,
    quadrant,
)


def test_opportunity_quadrants_are_two_dimensional() -> None:
    assert quadrant(0.8, 0.1, 0.5, 0.2) == "high_opportunity_low_downside"
    assert quadrant(0.8, 0.3, 0.5, 0.2) == "high_opportunity_high_downside"
    assert quadrant(0.2, 0.1, 0.5, 0.2) == "low_opportunity_low_downside"
    assert quadrant(0.2, 0.3, 0.5, 0.2) == "low_opportunity_high_downside"


def test_pareto_requires_better_reward_and_no_worse_downside() -> None:
    frame = pd.DataFrame(
        {"secid": ["A", "B", "C"], "expected_median": [0.1, 0.05, 0.12], "downside_axis": [0.1, 0.2, 0.3]}
    )
    pairs = {(left, right) for left, right, _, _ in pareto_pairs(frame)}
    assert ("A", "B") in pairs
    assert ("C", "A") not in pairs


def test_schema_has_reserve_abstention_and_no_magic_score() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    assert opportunity_status(con) == {"latest": None}
    columns = {row[0] for row in con.execute("DESCRIBE opportunity_candidates").fetchall()}
    assert {"candidate_type", "opportunity_axis", "downside_axis", "abstain", "abstention_reason"} <= columns


def test_full_opportunity_run_preserves_axes_pareto_and_cash_abstention() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE distribution_research_runs(run_id VARCHAR,cutoff DATE,status VARCHAR,"
        "finished_at TIMESTAMP)"
    )
    con.execute("CREATE TABLE ranking_research_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE scenario_research_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE timing_research_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute(
        "CREATE TABLE current_return_distributions(run_id VARCHAR,secid VARCHAR,"
        "horizon INTEGER,q50_return DOUBLE,q75_return DOUBLE,q25_return DOUBLE,"
        "q10_return DOUBLE,status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE current_portfolio_ranking(run_id VARCHAR,secid VARCHAR,"
        "horizon INTEGER,relative_rank DOUBLE,rank_low DOUBLE,rank_high DOUBLE,"
        "historical_oos DOUBLE)"
    )
    con.execute(
        "CREATE TABLE current_scenario_intelligence(run_id VARCHAR,secid VARCHAR,"
        "horizon INTEGER,applicability VARCHAR,status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE current_timing_intelligence(run_id VARCHAR,secid VARCHAR,"
        "horizon INTEGER,timing_status VARCHAR)"
    )
    for table, values in (
        ("distribution_research_runs", "('dist','2026-08-07','completed',current_timestamp)"),
        ("ranking_research_runs", "('rank','completed',current_timestamp)"),
        ("scenario_research_runs", "('scenario','completed',current_timestamp)"),
        ("timing_research_runs", "('timing','completed',current_timestamp)"),
    ):
        con.execute(f"INSERT INTO {table} VALUES {values}")
    for secid, median, downside, rank in (("AAA", 0.10, -0.05, 0.9), ("BBB", 0.03, -0.20, 0.2)):
        con.execute(
            "INSERT INTO current_return_distributions VALUES ('dist',?,60,?,.12,.01,?,'research_only')",
            [secid, median, downside],
        )
        con.execute(
            "INSERT INTO current_portfolio_ranking VALUES ('rank',?,60,?,?-.1,?+.1,.1)",
            [secid, rank, rank, rank],
        )
        con.execute(
            "INSERT INTO current_scenario_intelligence VALUES ('scenario',?,60,'medium','research_only')",
            [secid],
        )
        con.execute(
            "INSERT INTO current_timing_intelligence VALUES ('timing',?,60,'buy_now_not_beaten')", [secid]
        )
    result = core.run_opportunity_research(con)
    assert result["status"] == "completed"
    assert result["candidates"] == 3
    assert (
        con.execute(
            "SELECT count(*) FROM opportunity_pareto_dominance "
            "WHERE dominant_secid='AAA' AND dominated_secid='BBB'"
        ).fetchone()[0]
        == 1
    )
    cash = con.execute(
        "SELECT abstain,evidence_quality FROM opportunity_candidates WHERE secid='CASH'"
    ).fetchone()
    assert cash == (True, "insufficient_data")
    assert core.run_opportunity_research(con)["cached"] is True


def test_validated_rusfar_carry_is_annualized() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE macro_observations(series_id VARCHAR,value DOUBLE,observation_date DATE)")
    con.execute("INSERT INTO macro_observations VALUES ('RUSFAR',14.5,'2026-08-07')")
    carry, reason = core._reserve_carry(con)
    assert carry == 0.145
    assert "validated" in reason
