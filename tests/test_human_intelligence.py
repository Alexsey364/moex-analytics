import ast
from pathlib import Path

import duckdb

from moex_analytics.dashboard.navigation import BASIC_LABELS, navigation_pages, validate_basic_labels
from moex_analytics.portfolio_research.human_intelligence import (
    DDL,
    INTENTS,
    answer_question,
    confidence_engine,
    horizon_status,
)


def test_basic_navigation_is_russian_and_hides_internal_pages():
    basic = dict.fromkeys(BASIC_LABELS, lambda: None)
    advanced = {"Company Valuation": lambda: None, "Portfolio Action Map": lambda: None}
    assert validate_basic_labels(navigation_pages(basic, advanced))
    assert "Company Valuation" in navigation_pages(basic, advanced, advanced=True)


def test_basic_mode_does_not_render_raw_snapshot_id():
    text = Path("src/moex_analytics/dashboard/pages/human_portfolio.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    rendered_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "snapshot_id" not in rendered_literals


def test_confidence_is_decomposed_and_not_probability():
    confidence = confidence_engine(
        coverage=80,
        freshness_days=10,
        validated=6,
        alpha="conditional_candidate",
        regime="normal",
        agreement=75,
        sample=100,
        breaks=False,
    )
    assert 0 <= confidence.score <= 100
    assert confidence.label in {"низкая", "средняя", "выше средней", "высокая"}
    assert set(confidence.__dataclass_fields__) == {
        "coverage",
        "freshness",
        "fundamentals",
        "oos_validation",
        "regime_stability",
        "agreement",
        "sample_size",
        "structural_breaks",
    }


def test_horizons_do_not_emit_probabilities():
    assert horizon_status(None, 0.2)[1] == "? недостаточно данных"
    for momentum in (-0.2, 0.0, 0.2):
        assert "%" not in horizon_status(momentum, 0.2)[1]


def test_horizon_status_separates_machine_state_from_human_text():
    state, text = horizon_status(0.08, 0.2)
    assert state == "small_positive"
    assert text.startswith("↑")


def test_query_router_has_examples_and_rejects_unsupported():
    assert "Что по Сберу?" in INTENTS  # noqa: RUF001
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    answer = answer_question(con, "Придумай цену завтра")
    assert not answer["supported"]
    assert answer["conclusion"] == "Для этого вывода пока недостаточно данных"
    assert answer["supporting_evidence"] == []


def test_daily_report_schema_is_immutable():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    columns = {row[0] for row in con.execute("DESCRIBE human_daily_reports").fetchall()}
    assert {"analysis_cutoff", "input_hash", "immutable", "stale_warning"} <= columns


def test_launchers_are_safe_and_human_friendly():
    daily = Path("run_daily_analysis.bat").read_text(encoding="utf-8")
    batch = Path("START_MOEX_ANALYTICS.bat").read_text(encoding="utf-8")
    launcher = Path("src/moex_analytics/launcher.py").read_text(encoding="utf-8")
    assert "moex_analytics.launcher --daily-only" in daily
    assert '"quick-daily-update"' in launcher
    assert "port_launcher" in launcher
    assert "http://localhost:8501" in launcher
    assert "taskkill" not in batch.lower() and "taskkill" not in launcher.lower()
    assert batch.index("check_runtime_dependencies.py") < batch.index("moex_analytics.launcher")


def test_production_sber_engine_is_not_imported():
    text = Path("src/moex_analytics/portfolio_research/human_intelligence.py").read_text(encoding="utf-8")
    assert "sber_decision" not in text
    assert "BUY" not in text and "SELL" not in text
