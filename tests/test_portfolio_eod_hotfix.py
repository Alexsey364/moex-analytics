from datetime import date

import duckdb

from moex_analytics.portfolio_eod import core
from moex_analytics.portfolio_research.core import effective_segment_to
from moex_analytics.portfolio_research.daily_governance import _portfolio_prices_behind


class Response:
    status_code = 200
    url = "https://iss.moex.com/official?from=2026-08-01&till=2026-08-11&start=0"

    def raise_for_status(self):
        return None

    def json(self):
        return {"history": {"columns": ["BOARDID", "TRADEDATE", "SECID", "CLOSE"],
                "data": [["TQBR", "2026-08-07", "SBERP", 100],
                         ["TQBR", "2026-08-10", "SBERP", 101]]}}


class Session:
    def get(self, url, params, timeout):
        assert params["from"] == "2026-07-31"
        assert params["till"] == "2026-08-11"
        assert params["start"] == 0
        assert "history/engines/stock/markets/shares/boards/TQBR" in url
        return Response()


def _diagnostic_con():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE instrument_history_segments(canonical_secid VARCHAR,board VARCHAR,"
                "priority INTEGER,is_primary BOOLEAN)")
    con.execute("INSERT INTO instrument_history_segments VALUES ('SBERP','TQBR',100,true),"
                "('SBERP','EQBR',10,false)")
    con.execute("CREATE TABLE daily_prices(trade_date DATE,secid VARCHAR,board VARCHAR)")
    con.execute("INSERT INTO daily_prices VALUES (DATE '2026-08-07','SBERP','TQBR')")
    return con


def test_direct_diagnostic_uses_primary_board_per_instrument_cursor(monkeypatch):
    monkeypatch.setattr(core, "load_positions", lambda: [{"secid": "SBERP"}])
    rows = core.diagnose_portfolio_eod(_diagnostic_con(), Session(), date(2026, 8, 11))
    assert rows[0]["board"] == "TQBR"
    assert rows[0]["latest_local_eod"] == date(2026, 8, 7)
    assert rows[0]["latest_moex_eod"] == "2026-08-10"
    assert rows[0]["rows_returned"] == 2


def test_global_cutoff_does_not_smart_skip_older_stock(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("INSERT INTO canonical_daily_prices VALUES (DATE '2026-08-10','IMOEX'),"
                "(DATE '2026-08-07','SBERP')")
    monkeypatch.setattr("moex_analytics.portfolio_research.portfolio_editor.load_positions",
                        lambda: [{"secid": "SBERP"}])
    assert _portfolio_prices_behind(con, date(2026, 8, 10))


def test_layers_remain_distinct_and_quality_filter_does_not_hide_price():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE daily_prices(trade_date DATE,secid VARCHAR)")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("CREATE TABLE daily_returns(trade_date DATE,canonical_secid VARCHAR)")
    con.execute("INSERT INTO daily_prices VALUES (DATE '2026-08-10','SBERP')")
    assert con.execute("SELECT max(trade_date) FROM daily_prices").fetchone()[0] == date(2026, 8, 10)
    assert con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0] is None
    con.execute("INSERT INTO canonical_daily_prices VALUES (DATE '2026-08-10','SBERP')")
    assert con.execute("SELECT max(trade_date) FROM daily_returns").fetchone()[0] is None
    canonical_latest = con.execute(
        "SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    assert canonical_latest == date(2026, 8, 10)


def test_current_endpoint_fallback_fails_closed_on_semantic_mismatch():
    expected = date(2026, 8, 10)
    assert core.validate_current_fallback({"BOARDID": "TQBR", "TRADEDATE": expected,
        "CLOSE": 101, "TRADINGSESSION": 3}, expected, "TQBR")
    assert not core.validate_current_fallback({"BOARDID": "TQBR", "LAST": 101}, expected, "TQBR")
    assert not core.validate_current_fallback({"BOARDID": "SMAL", "TRADEDATE": expected,
        "CLOSE": 101}, expected, "TQBR")


def test_active_primary_segment_is_not_capped_by_stale_discovery_date():
    discovered = date(2026, 8, 7)
    today = date(2026, 8, 11)
    assert effective_segment_to(discovered, today, is_primary=True, incremental=True) == today
    assert effective_segment_to(discovered, today, is_primary=False, incremental=True) == discovered
