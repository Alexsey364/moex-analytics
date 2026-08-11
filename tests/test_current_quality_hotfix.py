from datetime import date

import duckdb

from moex_analytics.current_quality.core import audit_current_quality, quality_summary
from moex_analytics.portfolio_research.human_intelligence import compatible_opposing_evidence


def _con(price_date=date(2026, 8, 7)):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    for secid in ("SBERP", "LKOH", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX", "LSNGP", "X5"):
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?)", [price_date, secid])
    con.execute("CREATE TABLE trading_calendar(trade_date DATE,is_trading_day BOOLEAN)")
    for day in (date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11)):
        con.execute("INSERT INTO trading_calendar VALUES (?,true)", [day])
    con.execute("CREATE TABLE data_quality_issues(id INTEGER,secid VARCHAR,trade_date DATE,"
                "issue_type VARCHAR,description VARCHAR)")
    con.execute("INSERT INTO data_quality_issues VALUES (1,'SBERP',DATE '2019-01-01','gap','old')")
    return con


def test_weekend_and_open_session_do_not_require_missing_today_eod():
    con = _con(date(2026, 8, 7))
    weekend = audit_current_quality(con, date(2026, 8, 9), session_closed=False)
    assert weekend["market_cutoff"] == date(2026, 8, 7)
    assert weekend["critical"] == 0
    current = audit_current_quality(con, date(2026, 8, 10), session_closed=False)
    assert current["market_cutoff"] == date(2026, 8, 7)
    assert current["critical"] == 0


def test_stale_portfolio_price_is_current_blocker_but_old_warning_is_not():
    con = _con(date(2026, 8, 7))
    result = audit_current_quality(con, date(2026, 8, 11), session_closed=False)
    assert result["critical"] == 9
    assert result["prediction_blocking"] >= 9
    assert result["total_historical"] == result["unresolved"] == 1
    assert result["current_snapshot_relevant"] != result["total_historical"]
    issue = con.execute("SELECT dataset,instrument,severity,affects_current_snapshot,"
                        "affects_training,affects_prediction,reason FROM current_quality_issues "
                        "WHERE instrument='SBERP'").fetchone()
    assert issue == ("prices", "SBERP", "critical", True, False, True,
                     "portfolio price behind expected cutoff")


def test_session_closed_requires_current_eod_and_snapshot_is_versioned_idempotently():
    con = _con(date(2026, 8, 10))
    first = audit_current_quality(con, date(2026, 8, 10), session_closed=True)
    second = audit_current_quality(con, date(2026, 8, 10), session_closed=True)
    assert first["run_id"] == second["run_id"]
    assert first["critical"] == 0
    assert con.execute("SELECT count(*) FROM current_quality_runs").fetchone()[0] == 1
    assert quality_summary(con)["portfolio_relevant"] == 0


def test_old_qa_warning_is_not_current_without_compatible_snapshot_proof():
    con = duckdb.connect(":memory:")
    warning = "Incomplete normalized history; range is not production-ready"
    assert compatible_opposing_evidence(con, [warning, "Текущий риск"]) == ["Текущий риск"]


def test_current_blocking_warning_is_explained_in_russian():
    con = _con()
    audit_current_quality(con, date(2026, 8, 11), session_closed=False)
    con.execute("INSERT INTO current_quality_issues VALUES ((SELECT run_id FROM current_quality_runs "
        "ORDER BY created_at DESC LIMIT 1),'fundamentals','SBERP',DATE '2020-01-01',DATE '2025-12-31',"
        "'warning','active',true,true,true,'нет подтверждённой issuer-specific связи','fund-sberp')")
    result = compatible_opposing_evidence(
        con, ["Нет issuer-specific validated связи"])
    assert any("Блок fundamentals для SBERP" in item for item in result)
