from datetime import UTC, date, datetime

import duckdb
import pandas as pd
import pytest

from moex_analytics.actual_backfill.core import (
    _block_rows,
    _counts,
    _download,
    backfill_futures_specifications,
    backfill_official_fx,
    backfill_portfolio_dividends,
    backfill_universe_pilot,
    dividend_pair_consistency,
    ensure_schema,
    import_moex_annual_history,
    resolve_external_sources,
)
from moex_analytics.macro.models import Observation


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
    repeat = backfill_universe_pilot(con, UniverseClient(), 1)
    assert repeat["rows_inserted"] == 0
    assert con.execute("SELECT count(*) FROM tradable_on_date_universe").fetchone()[0] == 1


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


def test_dividend_pair_consistency_does_not_assume_parity():
    frame = pd.DataFrame([
        {"secid": "SBER", "record_date": date(2025, 7, 18), "dps": 34.84},
        {"secid": "SBERP", "record_date": date(2025, 7, 18), "dps": 34.84},
        {"secid": "SBER", "record_date": date(2024, 7, 11), "dps": 33.30},
    ])
    result = dividend_pair_consistency(frame, "SBER", "SBERP")
    assert result == {"dates": 2, "mismatches": 1, "consistent": False}


def test_official_fx_dividend_and_external_source_summaries(monkeypatch):
    con = base_connection()
    con.execute(
        """CREATE TABLE dividends(
        canonical_secid VARCHAR,registry_close_date DATE,declared_date DATE,payment_date DATE,
        dividend_per_share DOUBLE,currency VARCHAR,source VARCHAR,loaded_at TIMESTAMP,notes VARCHAR,
        PRIMARY KEY(canonical_secid,registry_close_date))"""
    )
    con.execute(
        """CREATE TABLE macro_observations(
        series_id VARCHAR,observation_date DATE,release_date DATE,available_from TIMESTAMPTZ,
        value DOUBLE,vintage VARCHAR,source VARCHAR,loaded_at TIMESTAMP,
        PRIMARY KEY(series_id,observation_date,vintage));
        CREATE TABLE macro_releases(
        series_id VARCHAR,observation_date DATE,release_date DATE,available_from TIMESTAMPTZ,
        vintage VARCHAR,source VARCHAR,loaded_at TIMESTAMP,
        PRIMARY KEY(series_id,observation_date,vintage));"""
    )
    con.execute(
        """CREATE TABLE external_factor_catalog(
        factor_id VARCHAR PRIMARY KEY,family VARCHAR,source VARCHAR,endpoint VARCHAR,
        license VARCHAR,access_class VARCHAR,timestamp_rule VARCHAR,pit_status VARCHAR,
        current_status VARCHAR,notes VARCHAR,checked_at TIMESTAMP)"""
    )
    monkeypatch.setattr(
        "moex_analytics.actual_backfill.core.cbr.CURRENCY_NAMES",
        {"R01235": ("cbr_usd_rub", "USD/RUB")},
    )
    observation = Observation(
        "cbr_usd_rub",
        date(2026, 8, 7),
        date(2026, 8, 7),
        datetime(2026, 8, 7, tzinfo=UTC),
        80.0,
        "initial",
        "CBR",
    )
    monkeypatch.setattr(
        "moex_analytics.actual_backfill.core.cbr.download_currency",
        lambda *args: [observation],
    )
    fx = backfill_official_fx(con, date(2026, 8, 7), date(2026, 8, 7))
    assert fx["cbr_usd_rub"]["inserted"] == 1

    class DividendClient:
        def dividends(self, secid):
            return [
                {
                    "canonical_secid": secid,
                    "registry_close_date": date(2025, 7, 18),
                    "dividend_per_share": 10.0,
                    "currency": "RUB",
                    "source": "MOEX",
                }
            ]

    dividends = backfill_portfolio_dividends(con, DividendClient())
    assert all(item["inserted"] == 1 for item in dividends.values())
    sources = resolve_external_sources(con)
    assert sources["paid_restricted"] == 2
    assert con.execute("SELECT count(*) FROM external_factor_catalog").fetchone()[0] == 6


def test_download_validation_counts_and_universe_failures():
    con = base_connection()
    assert _counts(con) == {"values": [], "documents": []}
    content, mime = _download(Session(), {"url": "https://example.test/report"})
    assert len(content) > 1000 and mime == "text/html"

    class Small(Session):
        def get(self, url, **kwargs):
            return Response(b"small")

    with pytest.raises(RuntimeError, match="too small"):
        _download(Small(), {"url": "https://example.test/small"})

    con.execute(
        """INSERT INTO historical_equity_universe VALUES
        ('EMPTY','EQBR',false,'common_share',NULL,NULL,'reg','RU0000000001'),
        ('ERROR','EQBR',false,'common_share',NULL,NULL,'reg','RU0000000002')"""
    )

    class PartialClient:
        def discover_history(self, secid):
            if secid == "ERROR":
                raise RuntimeError("source unavailable")
            return []

    result = backfill_universe_pilot(con, PartialClient(), 10)
    assert result["errors"] == 1
    assert result["rows_inserted"] == 0
