from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from moex_analytics.database import SCHEMA
from moex_analytics.fundamentals import documents
from moex_analytics.fundamentals.archive_parser import parse_html_text, parse_pdf_text
from moex_analytics.fundamentals.backtest import run as run_backtest
from moex_analytics.fundamentals.confidence import calculate_current, score
from moex_analytics.fundamentals.derived import build as build_derived
from moex_analytics.fundamentals.documents import archive_candidates, file_hash
from moex_analytics.fundamentals.fact_scenarios import calculate as calculate_facts
from moex_analytics.fundamentals.history import import_validated, install_regimes, validate
from moex_analytics.fundamentals.normalization import normalize
from moex_analytics.fundamentals.parser import parse_frame
from moex_analytics.fundamentals.pipeline import status


def test_catalogue_searches_before_2010_and_hash_is_stable():
    candidates = archive_candidates(1997, 1998)
    assert candidates[0]["year"] == 1997
    assert len(candidates) == 4
    assert file_hash(b"official") == file_hash(b"official")
    assert file_hash(b"official") != file_hash(b"changed")


def test_normalization_units():
    assert normalize(2, "тыс. руб.") == (2000, "RUB", "thousand_rub_to_rub")
    assert normalize(2, "млн руб.")[0] == 2_000_000
    assert normalize(2, "млрд руб.")[0] == 2_000_000_000
    assert normalize(25, "%") == (0.25, "ratio", "percent_to_ratio")
    with pytest.raises(ValueError):
        normalize(1, "unknown")


def test_official_html_parser_and_pdf_manual_review():
    html = """<table><tr><th>Код</th><th>Наименование</th><th>Пояснение</th><th>Значение</th></tr>
    <tr><td>26</td><td>Прибыль (убыток) за отчетный период</td><td></td><td>1 500 000</td></tr></table>"""
    rows = parse_html_text(html)
    assert rows[0]["metric_id"] == "net_profit"
    assert rows[0]["normalized_value"] == 1_500_000_000
    assert parse_pdf_text("image only", ("Активы",))[1] == "requires_manual_review"


def test_controlled_review_only_imports_validated_rows():
    frame = pd.DataFrame(
        [
            {
                "metric_id": "net_profit",
                "value": 100,
                "unit": "RUB",
                "accounting_standard": "RAS",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "publication_date": "2025-01-31",
                "source_document": "official.html",
                "source_page": "",
                "source_table": "1",
                "source_note": "",
                "revision_id": "original",
                "verified_by": "",
                "verification_status": "validated",
            },
            {
                "metric_id": "roe",
                "value": 0.2,
                "unit": "ratio",
                "accounting_standard": "RAS",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "publication_date": "2025-01-31",
                "source_document": "official.html",
                "source_page": "",
                "source_table": "1",
                "source_note": "",
                "revision_id": "original",
                "verified_by": "",
                "verification_status": "pending",
            },
        ]
    )
    rows = parse_frame(frame)
    assert len(rows) == 1 and rows[0].report_type == "annual"


def test_confidence_is_component_based_and_capped():
    result = score(
        key_metrics=20,
        total_key_metrics=8,
        age_days=30,
        has_ifrs=False,
        has_ras=True,
        consistency_checks=2,
        consistency_passed=2,
        has_shares=True,
        history_years=6,
        validated_releases=12,
        manual_ratio=0.1,
        ambiguities=1,
        stable_methodology=True,
    )
    assert 0 <= result["data_confidence"] <= 100
    assert result["valuation_confidence"] < result["data_confidence"]
    assert "completeness" in result["components"]


def test_new_schema_tables_exist():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    assert {"fundamental_documents", "fundamental_metric_values", "fundamental_backtest_results"} <= tables


class FakeResponse:
    text = "<title>Annual official form | Банк России</title><table></table>"
    content = text.encode()

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, *_args, **_kwargs):
        return FakeResponse()


def test_document_discovery_download_and_no_duplicate(monkeypatch, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    candidate = {
        "year": 2020,
        "archive": "202101",
        "form": "f807",
        "document_type": "RAS annual income",
        "source_url": "https://cbr.ru/official",
    }
    monkeypatch.setattr(documents, "archive_candidates", lambda: [candidate])
    assert documents.discover(con, FakeSession())["found"] == 1
    documents.discover(con, FakeSession())
    assert con.execute("SELECT count(*) FROM fundamental_documents").fetchone()[0] == 1
    result = documents.download(con, tmp_path, FakeSession())
    assert result["downloaded"] == 1
    assert status(con)["documents"] == 1


def _seed_history(con):
    dates = pd.bdate_range("2018-01-01", "2026-01-31")
    frame = pd.DataFrame(
        {
            "trade_date": dates.date,
            "canonical_secid": "SBER",
            "close": [180 + index * 0.03 for index in range(len(dates))],
        }
    )
    con.register("seed_prices", frame)
    con.execute("""INSERT INTO canonical_daily_prices(trade_date,canonical_secid,close)
                   SELECT trade_date,canonical_secid,close FROM seed_prices""")
    con.unregister("seed_prices")
    con.execute("""INSERT INTO dividends(canonical_secid,registry_close_date,dividend_per_share)
                   VALUES ('SBER','2025-07-18',34.84)""")
    for year_index, year in enumerate((2018, 2019, 2020, 2022, 2023, 2024)):
        publication = date(year + 1, 1, 31)
        for suffix, kind in (("i", "RAS annual income"), ("b", "RAS annual balance")):
            document_id = f"{year}-{suffix}"
            con.execute(
                """INSERT INTO fundamental_documents
              (document_id,secid,document_type,accounting_standard,period_start,period_end,
               publication_date,available_from,title,source_url,file_hash,mime_type,parser_version,
               processing_status,validation_status,revision_id,loaded_at)
              VALUES (?,'SBER',?,'RAS',?,?,?,?,'official annual',?,'hash','text/html','test',
                      'validated','validated','original',current_timestamp)""",
                [
                    document_id,
                    kind,
                    date(year, 1, 1),
                    date(year, 12, 31),
                    publication,
                    publication,
                    f"https://cbr.ru/{document_id}",
                ],
            )
        values = {
            "net_profit": (900 + year_index * 100) * 1e9,
            "profit_before_tax": (1100 + year_index * 110) * 1e9,
            "total_assets": (30_000 + year_index * 3000) * 1e9,
            "total_equity": (4000 + year_index * 400) * 1e9,
        }
        for metric, value in values.items():
            document_id = f"{year}-{'i' if metric in {'net_profit', 'profit_before_tax'} else 'b'}"
            con.execute(
                """INSERT INTO fundamental_metric_values
              (document_id,secid,metric_id,raw_value,raw_unit,normalized_value,normalized_unit,
               normalization_rule,accounting_standard,period_start,period_end,publication_date,
               available_from,revision_id,quality_status,loaded_at)
              VALUES (?,'SBER',?,?,'RUB',?,'RUB','identity','RAS',?,?,?,?,
                      'original','validated',current_timestamp)""",
                [
                    document_id,
                    metric,
                    value,
                    value,
                    date(year, 1, 1),
                    date(year, 12, 31),
                    publication,
                    publication,
                ],
            )


def test_validated_history_to_scenarios_and_backtest():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    _seed_history(con)
    install_regimes(con)
    assert import_validated(con) > 0
    assert validate(con)["validated"] == 12
    assert build_derived(con)["releases"] == 6
    confidence = calculate_current(con)
    assert confidence["data_confidence"] > 0
    config = Path(__file__).parents[1] / "config" / "sber_fundamental_history.yaml"
    assert calculate_facts(con, config)["rows"] == 12
    tested = run_backtest(con)
    assert tested["rows"] > 0 and tested["comparisons"] > 0
