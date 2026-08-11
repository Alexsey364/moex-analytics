import duckdb

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS
from moex_analytics.portfolio_verdict.core import action_policy, build_portfolio_verdicts


def test_red_requires_concentration_not_weak_model_output() -> None:
    action, _ = action_policy(
        stress=False, concentration=0.3, positive=0, negative=0, eligible_direction=False
    )
    assert action.startswith("🔴")
    weak, _ = action_policy(stress=False, concentration=0.1, positive=0, negative=1, eligible_direction=False)
    assert not weak.startswith("🔴")


def test_positive_action_requires_eligible_directional_evidence() -> None:
    action, _ = action_policy(
        stress=False, concentration=0.1, positive=2, negative=0, eligible_direction=True
    )
    assert action.startswith("🟢")
    no_direction, _ = action_policy(
        stress=False, concentration=0.1, positive=2, negative=0, eligible_direction=False
    )
    assert no_direction.startswith("🟡")


def test_action_policy_handles_stress_and_severe_data_without_false_red() -> None:
    caution, _ = action_policy(
        stress=True, concentration=0.1, positive=0, negative=2, eligible_direction=False
    )
    missing, _ = action_policy(
        stress=False,
        concentration=None,
        positive=0,
        negative=0,
        eligible_direction=False,
        severe_data=True,
    )
    assert caution.startswith("🟠")
    assert missing.startswith("⚪")


def test_full_verdict_uses_saved_evidence_and_current_real_snapshot() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE evidence_registry_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("CREATE TABLE whole_market_live_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("CREATE TABLE whole_market_state_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("INSERT INTO evidence_registry_runs VALUES ('evidence','2026-01-01')")
    con.execute("INSERT INTO whole_market_live_runs VALUES ('live','2026-01-01')")
    con.execute("INSERT INTO whole_market_state_runs VALUES ('state','2026-01-01')")
    con.execute(
        "CREATE TABLE whole_market_state_daily(run_id VARCHAR,trade_date DATE,market_state_label VARCHAR)"
    )
    con.execute("INSERT INTO whole_market_state_daily VALUES ('state','2026-01-01','stress')")
    con.execute(
        """CREATE TABLE portfolio_snapshots(snapshot_id VARCHAR,created_at TIMESTAMP,status VARCHAR)"""
    )
    con.execute("CREATE TABLE portfolio_positions(snapshot_id VARCHAR,secid VARCHAR,weight DOUBLE)")
    con.execute("INSERT INTO portfolio_snapshots VALUES ('portfolio','2026-01-01','real')")
    con.executemany(
        "INSERT INTO portfolio_positions VALUES ('portfolio',?,?)",
        [(secid, 0.30 if secid == "SBERP" else 0.0875) for secid in SECIDS],
    )
    con.execute(
        """CREATE TABLE live_stock_rank_forecasts(run_id VARCHAR,secid VARCHAR,horizon INTEGER,
        predicted_rank INTEGER,qualitative_state VARCHAR,status VARCHAR)"""
    )
    live = []
    for rank, secid in enumerate(SECIDS, start=1):
        live.extend(("live", secid, horizon, rank, "positive", "pending") for horizon in HORIZONS)
    con.executemany("INSERT INTO live_stock_rank_forecasts VALUES (?,?,?,?,?,?)", live)
    con.execute(
        """CREATE TABLE evidence_registry_blocks(run_id VARCHAR,instrument VARCHAR,horizon INTEGER,
        block_type VARCHAR,evidence_status VARCHAR,decision_eligible BOOLEAN,reason VARCHAR,
        relative_improvement DOUBLE,fold_stable BOOLEAN)"""
    )
    evidence = []
    for secid in SECIDS:
        for horizon in HORIZONS:
            evidence.extend(
                [
                    (
                        "evidence",
                        secid,
                        horizon,
                        "sector_conditioned",
                        "MODERATE_RESEARCH_EVIDENCE",
                        True,
                        "lower historical MAE",
                        0.1,
                        True,
                    ),
                    ("evidence", secid, horizon, "live", "LIVE_TOO_SMALL", False, "pending", None, None),
                ]
            )
    con.executemany("INSERT INTO evidence_registry_blocks VALUES (?,?,?,?,?,?,?,?,?)", evidence)
    result = build_portfolio_verdicts(con)
    assert result["horizon_verdicts"] == 45
    assert result["production_changes"] == 0
    assert build_portfolio_verdicts(con)["idempotent"] is True
    sber = con.execute(
        "SELECT portfolio_action FROM portfolio_final_verdicts WHERE instrument='SBERP'"
    ).fetchone()[0]
    assert sber.startswith("🔴")
    assert (
        con.execute(
            "SELECT count(*) FROM portfolio_final_verdicts WHERE portfolio_action LIKE '🟢%'"
        ).fetchone()[0]
        == 0
    )
