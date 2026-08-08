from datetime import date

import duckdb

from moex_analytics.actual_backfill.core import (
    _block_rows,
    backfill_futures_specifications,
    backfill_universe_pilot,
    ensure_schema,
    import_moex_annual_history,
)


def base_connection():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE issuer_fundamental_values(
          secid VARCHAR,metric VARCHAR,reporting_standard VARCHAR,period_start DATE,
          period_end DATE,publication_date DATE,available_from TIMESTAMP,source VARCHAR,
          document VARCHAR,page_table VARCHAR,raw_value DOUBLE,normalized_value DOUBLE,
          unit VARCHAR,validation_status VARCHAR,revision VARCHAR,
          PRIMARY KEY(secid,metric,period_end,reporting_standard,revision)
        );
        CREATE TABLE historical_equity_universe(
          secid VARCHAR,primary_board VARCHAR,is_traded BOOLEAN,instrument_type VARCHAR,
          first_trade DATE,last_trade DATE,regnumber VARCHAR,isin VARCHAR
        );
        CREATE TABLE expired_sber_futures(secid VARCHAR,expiration DATE);
    """)
    ensure_schema(con)
    return con


class Response:
    def __init__(self, content):
        self.content = content
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        return None


class Session:
    def get(self, url, **kwargs):
        text = "Operating income 43.57 Fee and commission income 19.80 EBITDA 33.60 "
        text += "Operating expenses 12.26 Net income 25.18 "
        text += "Fee and commission 21.21 Net interest 17.29 Operating costs excluding 10.48 "
        text += "EBITDA margin 72.8 Net income 20.26 basic EPS 9.02 "
        text += "Fee and commission income 23.6 Net interest 15.8 Adjusted EBITDA 28.7 "
        text += "margin 71.9 earnings per share 8.76 Operating income 43,229.5 "
        text += "Fee and commission income 26,181.4 Net interest 16,713.0 "
        text += "Operating expenses 15,435.3 Adjusted EBITDA 31,123.2 net profit 20.2 "
        return Response((f"<html>{url}" + text * 20 + "</html>").encode())


def test_multi_period_moex_history_and_document_inventory(monkeypatch, tmp_path):
    con = base_connection()
    monkeypatch.setattr("moex_analytics.actual_backfill.core.RAW_ROOT", tmp_path)
    result = import_moex_annual_history(con, Session())
    assert result["documents_downloaded"] == 4
    assert con.execute("SELECT count(DISTINCT period_end) FROM issuer_fundamental_values").fetchone()[0] == 4
    assert con.execute("SELECT count(*) FROM actual_document_inventory WHERE size_bytes>0").fetchone()[0] == 4


def test_manual_review_candidate_is_real_when_token_missing(monkeypatch, tmp_path):
    class Missing(Session):
        def get(self, url, **kwargs):
            return Response(("<html>official annual report" + "x" * 2000 + "</html>").encode())
    con = base_connection()
    monkeypatch.setattr("moex_analytics.actual_backfill.core.RAW_ROOT", tmp_path)
    result = import_moex_annual_history(con, Missing())
    assert result["manual_review"] > 0
    row = con.execute("SELECT document_url,source_hash,row_label,reason FROM actual_manual_review_candidates LIMIT 1").fetchone()
    assert row[0].startswith("https://www.moex.com/") and len(row[1]) == 64 and row[2] and row[3]


class UniverseClient:
    base_url = "https://iss.moex.com/iss"

    def discover_history(self, secid):
        return [{"engine": "stock", "market": "shares", "boardid": "EQBR", "history_from": date(2000, 1, 1)}]

    def history_pages(self, instrument, date_from, date_to):
        payload = {"history": {"columns": ["TRADEDATE", "CLOSE", "VOLUME", "VALUE"],
                               "data": [["2001-01-03", 10.0, 5.0, 50.0]]}}
        yield payload, 0, "official"

    def normalize_history(self, payload, secid, board, source):
        return [{"trade_date": "2001-01-03", "close": 10.0, "volume": 5.0,
                 "value": 50.0, "source": source}]


def test_tradable_on_date_universe_contains_inactive_security(monkeypatch, tmp_path):
    con = base_connection()
    con.execute("""INSERT INTO historical_equity_universe VALUES
                ('OLD','EQBR',false,'common_share',NULL,NULL,'1-01-00000-A','RU0000000000')""")
    monkeypatch.setattr("moex_analytics.actual_backfill.core.database_path", lambda: tmp_path / "db")
    (tmp_path / "db").write_bytes(b"db")
    result = backfill_universe_pilot(con, UniverseClient(), 1)
    assert result["rows_inserted"] == 1
    assert con.execute("SELECT inactive_at_audit FROM tradable_on_date_universe").fetchone()[0]


def test_official_futures_spec_is_saved_but_basis_not_auto_enabled():
    con = base_connection()
    con.execute("INSERT INTO expired_sber_futures VALUES ('SRZ6','2026-12-17')")

    class FuturesClient:
        base_url = "https://iss.moex.com/iss"
        def get_json(self, path, params):
            return {"securities": {"columns": ["ASSETCODE", "LOTSIZE", "MINSTEP", "STEPPRICE", "LASTTRADEDATE", "CURRENCYID"],
                                    "data": [["SBER", 100, 1, 1, "2026-12-17", "RUB"]]}}
        def save_raw(self, *args):
            return None

    result = backfill_futures_specifications(con, FuturesClient())
    assert result["validated"] == 1
    assert result["basis_enabled"] is False


def test_block_rows_preserves_official_columns():
    assert _block_rows({"columns": ["A", "B"], "data": [[1, 2]]}) == [{"A": 1, "B": 2}]
