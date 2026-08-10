from datetime import date

import duckdb
import pytest

from moex_analytics import returns
from moex_analytics.snapshot_freshness import core


def test_freshness_separates_daily_and_fundamental_cadence():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR)")
    for secid in core.PORTFOLIO:
        con.execute("INSERT INTO canonical_daily_prices VALUES ('2026-08-07',?)", [secid])
        if secid != "X5":
            con.execute("INSERT INTO daily_returns VALUES ('2026-08-07',?)", [secid])
    result = core.audit_current_freshness(con)
    assert result["status"] == "incomplete"
    x5 = con.execute("SELECT price_fresh,features_fresh,rank_eligible,reason FROM "
                     "instrument_freshness_states WHERE secid='X5'").fetchone()
    assert x5[:3] == (True, False, False)
    assert "daily_return_feature" in x5[3]
    assert core.audit_current_freshness(con)["cached"] is True


def test_all_daily_layers_make_complete_snapshot_without_fundamentals():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR)")
    for secid in core.PORTFOLIO:
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?)", [date(2026, 8, 7), secid])
        con.execute("INSERT INTO daily_returns VALUES (?,?)", [date(2026, 8, 7), secid])
    result = core.audit_current_freshness(con)
    assert result["status"] == "complete"
    assert result["eligible"] == 9


def test_interrupted_return_rebuild_preserves_existing_layer(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,"
                "close DOUBLE)")
    con.execute("CREATE TABLE dividends(registry_close_date DATE,canonical_secid VARCHAR,"
                "dividend_per_share DOUBLE)")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR,"
                "price_return DOUBLE,log_return DOUBLE,dividend_cash DOUBLE,dividend_return DOUBLE,"
                "total_return DOUBLE,total_return_index DOUBLE,calculation_version VARCHAR,"
                "calculated_at TIMESTAMP)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2026-01-01','A',100)")
    con.execute("INSERT INTO daily_returns VALUES "
                "('2025-01-01','OLD',NULL,NULL,0,NULL,NULL,1,'actual-dividends-v1',current_timestamp)")
    monkeypatch.setattr(returns, "calculate_rows", lambda *_: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError, match="stop"):
        returns.calculate_all(con)
    assert con.execute("SELECT canonical_secid FROM daily_returns").fetchone()[0] == "OLD"
