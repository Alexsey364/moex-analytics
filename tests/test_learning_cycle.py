import duckdb

import moex_analytics.learning_cycle.core as cycle
from moex_analytics.config import PROJECT_ROOT


def test_dataset_freeze_and_table_allowlist():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2025-01-01','SBERP')")
    assert len(cycle._dataset_id(con)) == 20
    try:
        cycle._latest_completed(con, "not_allowed")
    except ValueError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("dynamic table name must be allowlisted")


def test_checkpoint_is_resumable_and_status_is_explicit():
    con = duckdb.connect(":memory:")
    cycle.ensure_schema(con)
    con.execute(
        """INSERT INTO learning_cycle_runs VALUES
        ('run','data',current_timestamp,NULL,'running',NULL,NULL,FALSE,0,'test')"""
    )
    cycle._checkpoint(con, "run", 1, "completed", "component", {"reused": True})
    row = con.execute("SELECT status,component_run_id FROM learning_cycle_checkpoints").fetchone()
    assert row == ("completed", "component")
    assert cycle.learning_status(con)["latest"][1] == "running"


def test_portfolio_promotion_is_manual_and_requires_live_sample():
    assert cycle.MINIMUM_LIVE_N >= 100
    ddl = cycle.DDL.lower()
    assert "automatic_promotion boolean" in ddl
    assert "production_changes" in ddl


def test_empty_learning_status():
    con = duckdb.connect(":memory:")
    assert cycle.learning_status(con) == {"latest": None}


def test_full_cycle_checkpoints_and_resume_without_production(monkeypatch, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2025-01-01','SBERP')")
    con.execute(
        """CREATE TABLE tournament_runs(
        run_id VARCHAR,status VARCHAR,created_at TIMESTAMP)"""
    )
    con.execute("INSERT INTO tournament_runs VALUES ('tournament','completed',current_timestamp)")
    monkeypatch.setattr(cycle, "run_feature_learning", lambda _con: {"run_id": "feature"})
    monkeypatch.setattr(cycle, "run_market_memory", lambda _con: {"run_id": "memory"})
    monkeypatch.setattr(cycle, "run_calibration_audit", lambda _con: {"run_id": "calibration"})
    monkeypatch.setattr(cycle, "run_meta_learning", lambda _con: {"run_id": "meta"})
    monkeypatch.setattr(cycle, "run_portfolio_learning", lambda _con: {"run_id": "portfolio"})
    monkeypatch.setattr(cycle, "_build_champions", lambda _con, _run: 1)
    monkeypatch.setattr(cycle, "_build_journal", lambda _con, _run: 0)
    report = tmp_path / "report.md"
    monkeypatch.setattr(cycle, "_write_report", lambda _con, _run, _runtime: report)
    cycle.ensure_schema(con)
    dataset = cycle._dataset_id(con)
    con.execute(
        """INSERT INTO learning_cycle_runs VALUES
        ('resume',?,current_timestamp,NULL,'interrupted',NULL,NULL,FALSE,0,'test')""",
        [dataset],
    )
    cycle._checkpoint(con, "resume", 1, "completed", "tournament", {"reused": True})
    result = cycle.run_full_learning_cycle(con)
    assert result["resumed"] is True
    assert result["production_changes"] == 0
    assert (
        con.execute("SELECT count(*) FROM learning_cycle_checkpoints WHERE status='completed'").fetchone()[0]
        == 7
    )
    assert cycle.learning_status(con)["latest"][1] == "completed"


def test_controlled_daily_never_retrains_or_promotes(monkeypatch):
    con = duckdb.connect(":memory:")
    monkeypatch.setattr(
        cycle,
        "update_forecast_scorecards",
        lambda _con: {
            "capture": {"created": 2},
            "evaluation": {"matured": 1},
            "status": {"live_status": "learning"},
        },
    )
    monkeypatch.setattr(cycle, "build_governance_metrics", lambda _con: {"rolling_metrics": 4})
    result = cycle.run_controlled_daily(con)
    assert result["retrained"] is False
    assert result["production_changes"] == 0
    assert result["new_forecasts"] == 2


def test_champion_review_journal_and_report_are_fail_closed(tmp_path, monkeypatch):
    con = duckdb.connect(":memory:")
    cycle.ensure_schema(con)
    con.execute("CREATE TABLE tournament_runs(run_id VARCHAR,status VARCHAR,created_at TIMESTAMP)")
    con.execute(
        """CREATE TABLE tournament_leaderboard(
        run_id VARCHAR,secid VARCHAR,horizon INTEGER,winner VARCHAR,status VARCHAR)"""
    )
    con.execute(
        """CREATE TABLE tournament_results(
        run_id VARCHAR,secid VARCHAR,horizon INTEGER,model VARCHAR,split VARCHAR,
        advantage DOUBLE,probability_allowed BOOLEAN)"""
    )
    con.execute(
        """CREATE TABLE forecast_registry(
        forecast_id VARCHAR,secid VARCHAR,horizon_sessions INTEGER,model_version VARCHAR,
        cutoff DATE,regime_status VARCHAR)"""
    )
    con.execute(
        """CREATE TABLE forecast_outcomes(
        forecast_id VARCHAR,actual_return DOUBLE,direction_correct BOOLEAN,outcome_status VARCHAR)"""
    )
    con.execute("INSERT INTO tournament_runs VALUES ('source','completed',current_timestamp)")
    con.execute(
        "INSERT INTO tournament_leaderboard VALUES ('source','SBERP',20,'ensemble','shadow_candidate')"
    )
    con.execute(
        """INSERT INTO tournament_results VALUES
        ('source','SBERP',20,'ensemble','untouched_holdout',0.03,FALSE)"""
    )
    con.execute(
        "INSERT INTO forecast_registry VALUES ('forecast','SBERP',20,'ensemble','2025-01-01','normal')"
    )
    con.execute("INSERT INTO forecast_outcomes VALUES ('forecast',0.02,TRUE,'matured')")
    assert cycle._build_champions(con, "cycle") == 1
    assert cycle._build_journal(con, "cycle") == 1
    review = con.execute("SELECT status,automatic_promotion FROM learning_promotion_review").fetchone()
    assert review == ("continue_shadow", False)
    monkeypatch.chdir(tmp_path)
    report = cycle._write_report(con, "cycle", 1.25)
    assert "Production changes: **0**" in report.read_text(encoding="utf-8")


def test_champion_builder_without_tournament_is_empty():
    con = duckdb.connect(":memory:")
    cycle.ensure_schema(con)
    con.execute("CREATE TABLE tournament_runs(run_id VARCHAR,status VARCHAR,created_at TIMESTAMP)")
    assert cycle._build_champions(con, "cycle") == 0


def test_windows_research_launcher_requires_python_312():
    launcher = (PROJECT_ROOT / "run_full_learning_cycle.bat").read_text(encoding="utf-8")
    assert "sys.version_info[:2] == (3,12)" in launcher
    assert "-m moex_analytics.cli run-full-learning-cycle" in launcher
