import duckdb

from moex_analytics.portfolio_research.intelligence import DDL, FAMILIES, SOURCES, ensure_schema
from moex_analytics.portfolio_research.portfolio_v14 import parse_local_portfolio


def test_cash_unknown_is_preserved(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("mode: real\ncash: null\npositions: []\n", encoding="utf-8")
    assert parse_local_portfolio(p)["cash"] is None


def test_reference_value_not_cash(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("mode: demo\ncash: null\naccount_reference_value: 10\npositions: []\n", encoding="utf-8")
    cfg = parse_local_portfolio(p)
    assert cfg["cash"] is None and cfg["account_reference_value"] == 10


def test_all_issuers_have_official_sources():
    assert set(SOURCES) == {"X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX"}


def test_all_issuers_have_valuation_family():
    assert set(FAMILIES) == set(SOURCES)


def test_sources_are_not_aggregators():
    assert all("smart-lab" not in x[1] for sources in SOURCES.values() for x in sources)


def test_intelligence_schema():
    c = duckdb.connect(":memory:")
    ensure_schema(c)
    tables = {x[0] for x in c.execute("select table_name from information_schema.tables").fetchall()}
    assert {
        "portfolio_reconciliation",
        "issuer_fundamental_values",
        "portfolio_action_map",
        "portfolio_intelligence_shadow",
    } <= tables


def test_action_vocabulary_has_no_buy_sell():
    text = (
        __import__("pathlib").Path(__file__).parents[1]
        / "src/moex_analytics/portfolio_research/intelligence.py"
    ).read_text()
    assert '"BUY"' not in text and '"SELL"' not in text


def test_reference_reconciliation_status():
    c = duckdb.connect(":memory:")
    c.execute(DDL)
    assert (
        c.execute(
            "select count(*) from information_schema.tables where table_name='portfolio_reconciliation'"
        ).fetchone()[0]
        == 1
    )
