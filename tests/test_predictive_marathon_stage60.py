import duckdb
import pytest

from moex_analytics.predictive_marathon import core


def test_marathon_schema_and_checkpoint_resume():
    con = duckdb.connect(":memory:")
    core.ensure_schema(con)
    calls = []
    first = core._checkpoint(con, "run", "targets", lambda: calls.append(1) or {"rows": 2})
    second = core._checkpoint(con, "run", "targets", lambda: calls.append(2) or {"rows": 3})
    assert first == second == {"rows": 2}
    assert calls == [1]
    assert (
        con.execute("SELECT status FROM predictive_marathon_checkpoints WHERE run_id='run'").fetchone()[0]
        == "completed"
    )


def test_marathon_steps_and_launcher_are_explicit():
    assert core.STEPS == (
        "targets",
        "ranking",
        "distribution",
        "scenario",
        "timing",
        "opportunity",
        "portfolio",
        "validation",
        "current",
        "report",
    )
    text = (core.PROJECT_ROOT / "RUN_PREDICTIVE_RESEARCH_MARATHON.bat").read_text()
    assert "run-predictive-research-marathon" in text
    assert "Production changes: 0" in text


def test_marathon_run_metadata_insert_uses_named_columns():
    source = (core.PROJECT_ROOT / "src/moex_analytics/predictive_marathon/core.py").read_text()
    assert "(run_id,started_at,status,completed_steps_json,max_runtime_hours,version,details_json)" in source
    assert "INSERT INTO predictive_marathon_runs VALUES" not in source
    assert "invalid_incomplete_universe" in source
    assert "untouched_holdout_frozen" in source


def _orchestrator_db():
    con = duckdb.connect(":memory:")
    core.ensure_schema(con)
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2026-08-07')")
    return con


def test_fresh_marathon_orders_checkpoints_and_completed_run_is_cached(monkeypatch, tmp_path):
    con = _orchestrator_db()
    calls = []
    monkeypatch.setattr(core, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(core, "_validated_targets", lambda _con: calls.append("targets") or {})
    monkeypatch.setattr(core, "run_ranking_research", lambda _con: calls.append("ranking") or {})
    monkeypatch.setattr(core, "run_distribution_research", lambda _con: calls.append("distribution") or {})
    monkeypatch.setattr(core, "run_scenario_research", lambda _con: calls.append("scenario") or {})
    monkeypatch.setattr(core, "run_timing_research", lambda _con: calls.append("timing") or {})
    monkeypatch.setattr(core, "run_opportunity_research", lambda _con: calls.append("opportunity") or {})
    monkeypatch.setattr(core, "run_portfolio_optimizer", lambda _con: calls.append("portfolio") or {})
    monkeypatch.setattr(core, "run_multi_horizon_research", lambda _con: calls.append("validation") or {})
    monkeypatch.setattr(core, "_current_summary", lambda _con: calls.append("current") or {})
    monkeypatch.setattr(core, "build_report", lambda _con, _run: "evidence")
    result = core.run_predictive_research_marathon(con, 1)
    assert result["status"] == "completed"
    assert calls == list(core.STEPS[:-1])
    assert (tmp_path / "reports/predictive_research_marathon.md").read_text() == "evidence"
    assert core.run_predictive_research_marathon(con, 1)["cached"] is True
    assert con.execute("SELECT count(*) FROM predictive_marathon_checkpoints").fetchone()[0] == 10


def test_failed_checkpoint_is_recorded_and_resume_skips_completed(monkeypatch, tmp_path):
    con = _orchestrator_db()
    monkeypatch.setattr(core, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(core, "_validated_targets", lambda _con: {"ok": True})
    attempts = {"ranking": 0}

    def ranking(_con):
        attempts["ranking"] += 1
        if attempts["ranking"] == 1:
            raise RuntimeError("controlled failure")
        return {"ok": True}

    monkeypatch.setattr(core, "run_ranking_research", ranking)
    for name in (
        "run_distribution_research",
        "run_scenario_research",
        "run_timing_research",
        "run_opportunity_research",
        "run_portfolio_optimizer",
        "run_multi_horizon_research",
    ):
        monkeypatch.setattr(core, name, lambda _con: {"ok": True})
    monkeypatch.setattr(core, "_current_summary", lambda _con: {"ok": True})
    monkeypatch.setattr(core, "build_report", lambda _con, _run: "resumed")
    with pytest.raises(RuntimeError, match="controlled failure"):
        core.run_predictive_research_marathon(con, 1)
    assert (
        con.execute("SELECT status FROM predictive_marathon_checkpoints WHERE step='ranking'").fetchone()[0]
        == "failed"
    )
    result = core.run_predictive_research_marathon(con, 1)
    assert result["status"] == "completed"
    assert attempts["ranking"] == 2


def test_configured_runtime_limit_is_resumable(monkeypatch):
    con = _orchestrator_db()
    clocks = iter((0.0, 3601.0))
    monkeypatch.setattr(core.time, "perf_counter", lambda: next(clocks))
    with pytest.raises(TimeoutError, match="rerun to resume"):
        core.run_predictive_research_marathon(con, 0)
    assert core.marathon_status(con)["status"] == "failed"


def _universe_db(source_stocks, target_stocks):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_returns(canonical_secid VARCHAR,trade_date DATE)")
    con.executemany(
        "INSERT INTO daily_returns VALUES (?,?)",
        [(secid, last_date) for secid, last_date in source_stocks],
    )
    con.execute(
        "CREATE TABLE predictive_target_runs(run_id VARCHAR,status VARCHAR,"
        "observation_rows BIGINT,entry_rows BIGINT,cutoff DATE,finished_at TIMESTAMP)"
    )
    con.execute("CREATE TABLE predictive_target_observations(run_id VARCHAR,secid VARCHAR)")
    con.execute(
        "INSERT INTO predictive_target_runs VALUES "
        "('partial','completed',10,2,'2026-08-07',current_timestamp),"
        "('complete','completed',90,20,'2026-08-06',current_timestamp-INTERVAL 1 DAY)"
    )
    con.executemany(
        "INSERT INTO predictive_target_observations VALUES ('partial',?)",
        [(secid,) for secid in target_stocks],
    )
    con.executemany(
        "INSERT INTO predictive_target_observations VALUES ('complete',?)",
        [(secid,) for secid in sorted(core.PORTFOLIO)],
    )
    con.execute("CREATE TABLE ranking_research_runs(run_id VARCHAR,target_run_id VARCHAR,status VARCHAR)")
    con.execute("CREATE TABLE distribution_research_runs(ranking_run_id VARCHAR,status VARCHAR)")
    con.execute("CREATE TABLE timing_research_runs(ranking_run_id VARCHAR,status VARCHAR)")
    con.execute(
        "CREATE TABLE opportunity_research_runs(run_id VARCHAR,ranking_run_id VARCHAR,status VARCHAR)"
    )
    con.execute("CREATE TABLE cash_aware_optimizer_runs(opportunity_run_id VARCHAR,status VARCHAR)")
    con.execute("CREATE TABLE multi_horizon_runs(opportunity_run_id VARCHAR,status VARCHAR)")
    con.execute("INSERT INTO ranking_research_runs VALUES ('rank-partial','partial','completed')")
    con.execute("INSERT INTO distribution_research_runs VALUES ('rank-partial','completed')")
    con.execute("INSERT INTO timing_research_runs VALUES ('rank-partial','completed')")
    con.execute("INSERT INTO opportunity_research_runs VALUES ('opp-partial','rank-partial','completed')")
    con.execute("INSERT INTO cash_aware_optimizer_runs VALUES ('opp-partial','completed')")
    con.execute("INSERT INTO multi_horizon_runs VALUES ('opp-partial','completed')")
    return con


@pytest.mark.parametrize("missing", [{"X5"}, {"X5", "SBERP", "TATNP"}])
def test_incomplete_universe_is_quarantined_without_mixing(missing):
    present = sorted(core.PORTFOLIO - missing)
    source = [(secid, "2026-08-07") for secid in present]
    con = _universe_db(source, present)
    result = core._validated_targets(con)
    assert result["run_id"] == "complete"
    assert result["fallback"] is True
    assert set(result["missing"]) == missing
    assert con.execute("SELECT status FROM predictive_target_runs WHERE run_id='partial'").fetchone()[0] == (
        "invalid_incomplete_universe"
    )
    assert con.execute("SELECT status FROM opportunity_research_runs").fetchone()[0] == (
        "invalid_incomplete_universe"
    )


def test_duplicate_and_unexpected_stocks_do_not_replace_complete_membership(monkeypatch):
    source = [(secid, "2026-08-07") for secid in core.PORTFOLIO]
    source += [("X5", "2026-08-07"), ("UNEXPECTED", "2026-08-07")]
    con = _universe_db(source, sorted(core.PORTFOLIO))
    monkeypatch.setattr(core, "build_predictive_targets", lambda _con: {"run_id": "complete"})
    result = core._validated_targets(con)
    assert result == {"run_id": "complete"}


def test_stale_required_stock_forces_full_snapshot_fallback():
    source = [(secid, "2026-07-01" if secid == "SBERP" else "2026-08-07") for secid in core.PORTFOLIO]
    con = _universe_db(source, sorted(core.PORTFOLIO))
    result = core._validated_targets(con)
    assert result["run_id"] == "complete"
    assert "SBERP" in result["missing"]


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class _ReportConnection:
    def execute(self, query, _params=None):
        if "FROM ranking_scorecards" in query:
            return _Rows([(5, "ridge", 100, 20, 0.1, 0.02, 0.18, 0.01, "SHADOW_CANDIDATE")])
        if "FROM distribution_scorecards" in query:
            return _Rows([(5, "conformal", 100, 0.03, 0.8, 0.001, "SHADOW_CANDIDATE")])
        if "FROM timing_policy_scorecards" in query:
            return _Rows([(5, "BUY_NOW", 100, 0.01, -0.05, "NO_EVIDENCE")])
        if "GROUP BY secid ORDER BY secid" in query:
            return _Rows([("SBERP", 6, 2)])
        if "FROM portfolio_allocation_plans" in query:
            return _Rows([(100_000.0, "CASH_PREFERRED", '{"CASH":100000}', 100_000.0)])
        if "AND horizon=60 ORDER BY" in query:
            return _Rows([("SBERP", 0.5, 0.02, -0.1, "wait", "research_oos")])
        if "FROM historical_analogs_v3" in query:
            return _Rows([("SBERP", "2020-01-01", 80.0)])
        if "FROM portfolio_optimizer_backtests" in query:
            return _Rows([("cash_aware_full", 10, 1.0, 0.0, 0.02)])
        if "FROM horizon_feature_ablation" in query:
            return _Rows([("rejected", 4)])
        if "FROM forecast_registry" in query:
            return _Rows([(10, 8, 2)])
        raise AssertionError(query)


def test_evidence_report_uses_database_results_and_all_required_sections():
    report = core.build_report(_ReportConnection(), "run-1")
    for required in (
        "Frozen methodology",
        "Ranking holdout",
        "Distribution holdout",
        "Timing experiment",
        "Current nine-stock research",
        "Cash-aware additions",
        "Historical portfolio comparison",
        "Ablation and multiplicity",
        "Live separation",
        "Production changes: **0**",
    ):
        assert required in report
    assert "SBERP" in report
    assert "pending 8, matured 2" in report
    assert "CASH_PREFERRED" in report
