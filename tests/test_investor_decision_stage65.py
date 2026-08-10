import duckdb

from moex_analytics.investor_decision import core


def _decision_db():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE rank_group_runs(run_id VARCHAR,cutoff DATE,status VARCHAR,"
                "created_at TIMESTAMP)")
    con.execute("CREATE TABLE current_rank_groups(run_id VARCHAR,secid VARCHAR,horizon INTEGER,"
                "group_label VARCHAR)")
    con.execute("CREATE TABLE composite_rank_groups(run_id VARCHAR,secid VARCHAR,"
                "relative_conviction VARCHAR)")
    con.execute("CREATE TABLE snapshot_freshness_runs(run_id VARCHAR,status VARCHAR,"
                "created_at TIMESTAMP)")
    con.execute("CREATE TABLE instrument_freshness_states(run_id VARCHAR,secid VARCHAR,"
                "rank_eligible BOOLEAN,reason VARCHAR)")
    con.execute("CREATE TABLE live_ranking_snapshots(snapshot_id VARCHAR,secid VARCHAR)")
    con.execute("CREATE TABLE live_ranking_outcomes(snapshot_id VARCHAR,status VARCHAR)")
    con.execute("CREATE TABLE opportunity_research_runs(run_id VARCHAR,status VARCHAR,"
                "finished_at TIMESTAMP)")
    con.execute("CREATE TABLE opportunity_candidates(run_id VARCHAR,secid VARCHAR,"
                "tail_downside DOUBLE,abstain BOOLEAN,portfolio_weight DOUBLE,"
                "risk_contribution DOUBLE,candidate_type VARCHAR)")
    con.execute("INSERT INTO rank_group_runs VALUES "
                "('groups','2026-08-07','completed',current_timestamp)")
    con.execute("INSERT INTO snapshot_freshness_runs VALUES "
                "('fresh','complete',current_timestamp)")
    con.execute("INSERT INTO opportunity_research_runs VALUES "
                "('opportunity','completed',current_timestamp)")
    for secid, eligible, abstain in (("A", True, False), ("B", False, True)):
        for horizon in (60, 120, 250):
            label = "TOP GROUP" if secid == "A" else "BOTTOM GROUP"
            con.execute("INSERT INTO current_rank_groups VALUES ('groups',?,?,?)",
                        [secid, horizon, label])
        con.execute("INSERT INTO composite_rank_groups VALUES ('groups',?,'higher')", [secid])
        con.execute("INSERT INTO instrument_freshness_states VALUES ('fresh',?,?,?)",
                    [secid, eligible, "eligible" if eligible else "missing"])
        con.execute("INSERT INTO opportunity_candidates VALUES "
                    "('opportunity',?,-.1,?,.1,.1,'equity')", [secid, abstain])
    return con


def test_status_never_turns_green_when_abstaining_or_incomplete():
    groups = {60: "TOP GROUP", 120: "TOP GROUP", 250: "MIDDLE"}
    assert core._status(groups, "higher", "complete", True)[0] == "YELLOW"
    assert core._status(groups, "higher", "incomplete", False)[0] == "GRAY"
    assert core._status(groups, "higher", "complete", False)[0] == "GREEN"


def test_saved_question_uses_only_distilled_evidence():
    con = duckdb.connect(":memory:")
    core.ensure_schema(con)
    con.execute("INSERT INTO investor_decision_runs VALUES "
                "('run','2026-08-07',current_timestamp,'completed',1,'{}',true)")
    con.execute("INSERT INTO distilled_investor_views VALUES "
                "('run','2026-08-07','TRNFP','YELLOW','наблюдать','MIDDLE','TOP GROUP',"
                "'BOTTOM GROUP','low',NULL,'scenario','no_edge','cash','complete',0,'[]','[]',"
                "'→ без изменений',true)")
    answer = core.answer_saved_question(con, "Почему TRNFP выше SBERP?")
    assert "TRNFP" in answer
    assert "live N=0" in answer
    reserve = core.answer_saved_question(con, "Почему программа оставляет 100 000 ₽ в резерве?")
    assert "CASH_PREFERRED" in reserve


def test_full_distillation_is_immutable_and_status_is_available():
    con = _decision_db()
    result = core.build_investor_decisions(con)
    assert result["rows"] == 2
    assert result["status_counts"] == {"GREEN": 1, "GRAY": 1}
    assert core.build_investor_decisions(con)["cached"] is True
    assert core.investor_status(con)["status"] == "completed"


def test_final_report_uses_saved_validation_and_views(tmp_path, monkeypatch):
    con = _decision_db()
    core.build_investor_decisions(con)
    con.execute("CREATE TABLE long_horizon_ranking_runs(run_id VARCHAR,status VARCHAR,"
                "finished_at TIMESTAMP)")
    con.execute("CREATE TABLE long_horizon_ranking_validation(run_id VARCHAR,horizon INTEGER,"
                "rank_ic DOUBLE,ci_low DOUBLE,ci_high DOUBLE,top_bottom_spread_after_costs DOUBLE,"
                "turnover DOUBLE,status VARCHAR,context_type VARCHAR)")
    con.execute("INSERT INTO long_horizon_ranking_runs VALUES "
                "('validation','completed',current_timestamp)")
    con.execute("INSERT INTO long_horizon_ranking_validation VALUES "
                "('validation',60,.1,.05,.15,.03,.2,'ROBUST_RELATIVE_EDGE','all')")
    monkeypatch.setattr(core, "PROJECT_ROOT", tmp_path)
    result = core.write_final_report(con)
    report = tmp_path / "reports" / "stage65_long_horizon_ranking_evidence.md"
    assert result["report"] == str(report)
    assert "Production changes = 0" in report.read_text(encoding="utf-8")
