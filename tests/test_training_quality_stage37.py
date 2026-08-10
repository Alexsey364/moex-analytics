from types import SimpleNamespace

import duckdb
import requests

from moex_analytics.training_quality.fundamental_recovery import (
    _audit_source,
    _document_links,
    _review_candidates,
    fundamental_recovery_status,
    recover_official_fundamentals,
)
from moex_analytics.training_quality.schema import DDL


def test_document_discovery_stays_on_official_host_and_accepts_pdf():
    html = b'<a href="/reports/annual-2020.pdf">a</a><a href="https://evil.test/x.pdf">x</a>'
    assert _document_links("https://issuer.test/investors", html) == [
        "https://issuer.test/reports/annual-2020.pdf"
    ]


def test_source_audit_records_verified_tls_and_hash(monkeypatch, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    response = SimpleNamespace(
        content=b'<a href="annual-report.pdf">report</a>',
        headers={"content-type": "text/html"},
        ok=True,
        status_code=200,
        url="https://issuer.test/investors/",
    )
    session = SimpleNamespace(get=lambda *args, **kwargs: response)
    monkeypatch.setattr(
        "moex_analytics.training_quality.fundamental_recovery.PROJECT_ROOT", tmp_path
    )
    result = _audit_source(con, "run", "TEST", response.url, session)
    assert result["reachable"] is True
    assert result["links"] == ["https://issuer.test/investors/annual-report.pdf"]
    assert con.execute("SELECT tls_status FROM source_resolution_registry").fetchone()[0] == "verified"


def test_recovery_orchestrator_is_leakage_gated_and_production_frozen(monkeypatch, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.execute("""CREATE TABLE issuer_fundamental_values(
        issuer VARCHAR,secid VARCHAR,period_end DATE,publication_date DATE,
        available_from TIMESTAMP,validation_status VARCHAR)""")
    con.execute("""INSERT INTO issuer_fundamental_values VALUES
        ('TEST','TEST',DATE '2020-12-31',DATE '2021-03-01',
        TIMESTAMP '2021-03-01','validated')""")
    monkeypatch.setattr(
        "moex_analytics.training_quality.fundamental_recovery.SOURCE_CANDIDATES",
        {"TEST": ["https://issuer.test"]},
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.fundamental_recovery._audit_source",
        lambda *args, **kwargs: {"reachable": True, "links": ["x.pdf"],
                                "downloaded": 1, "request": 1},
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.fundamental_recovery.deepen_pit_fundamentals",
        lambda *args, **kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.fundamental_recovery._review_candidates",
        lambda *args, **kwargs: 1,
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.fundamental_recovery.PROJECT_ROOT", tmp_path
    )
    result = recover_official_fundamentals(con, download=False)
    assert result["leakage_violations"] == 0
    assert result["production_changes"] == 0
    assert result["validated_periods_before"] == result["validated_periods_after"] == 1
    con.execute("""CREATE TABLE stage30_fundamental_coverage(
        issuer VARCHAR,validated_periods INTEGER,coverage_status VARCHAR)""")
    con.execute("INSERT INTO stage30_fundamental_coverage VALUES ('TEST',1,'insufficient_sample')")
    assert fundamental_recovery_status(con)["latest"][0] == result["run_id"]


def test_review_package_contains_real_candidate_values(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.execute("""CREATE TABLE issuer_fundamental_values(
        issuer VARCHAR,secid VARCHAR,period_end DATE,metric VARCHAR,document VARCHAR,
        page_table VARCHAR,raw_value DOUBLE,raw_unit VARCHAR,unit VARCHAR,source_hash VARCHAR,
        validation_status VARCHAR)""")
    con.execute("""INSERT INTO issuer_fundamental_values VALUES
        ('TEST','TEST',DATE '2020-12-31','revenue','report.pdf','p10/table2',100,'RUB','RUB',
        'abc','manual_review')""")
    path = tmp_path / "review.json"
    assert _review_candidates(con, "run", path) == 1
    assert '"candidate_value": 100.0' in path.read_text(encoding="utf-8")


def test_tls_failure_is_recorded_without_disabling_validation():
    con = duckdb.connect(":memory:")
    con.execute(DDL)

    def fail(*args, **kwargs):
        raise requests.exceptions.SSLError("certificate verify failed")

    result = _audit_source(
        con, "run", "TEST", "https://issuer.test", SimpleNamespace(get=fail)
    )
    assert result["reachable"] is False
    assert con.execute("SELECT tls_status FROM source_resolution_registry").fetchone()[0] == (
        "validation_failed"
    )
