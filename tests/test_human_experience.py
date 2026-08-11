from pathlib import Path

import pandas as pd
import pytest

from moex_analytics.dashboard.human_experience import (
    FORBIDDEN_BASIC_TERMS,
    action_text,
    horizon_state,
    human_status,
    percent,
    portfolio_verdict,
    rubles,
    security_name,
    short_date,
)
from moex_analytics.dashboard.visual_semantics import forecast_marker


@pytest.mark.parametrize(
    ("secid", "name"),
    [("SBERP", "Сбербанк ап"), ("TRNFP", "Транснефть ап"), ("MOEX", "Московская биржа")],
)
def test_basic_security_names_are_human_readable(secid, name):
    assert security_name(secid) == name


@pytest.mark.parametrize(
    "internal",
    ["NO_EVIDENCE", "WEAK_EVIDENCE", "SHADOW_CANDIDATE", "requires_more_history"],
)
def test_known_internal_statuses_are_translated(internal):
    assert internal not in human_status(internal)


def test_russian_number_formatting():
    assert rubles(286263.8, 0) == "286 264 ₽"
    assert percent(0.0393) == "+3,93%"
    assert percent(-0.054, 1) == "−5,4%"  # noqa: RUF001
    assert short_date("2026-08-10") == "10.08.2026"


def test_portfolio_verdict_is_deterministic_and_conservative():
    assert portfolio_verdict(["YELLOW"])[0].startswith("🟡")
    assert portfolio_verdict(["RED", "GREEN"])[0].startswith("🔴")
    assert portfolio_verdict(["GRAY"])[0].startswith("⚪")


def test_central_decision_semantics_are_consistent():
    assert action_text("do_not_increase") == "🟠 Пока не увеличивать"
    assert horizon_state("небольшой негативный перевес") == "🟠 слабее альтернатив"
    assert horizon_state("нейтрально") == "🟡 смешанная картина"


def test_forecast_marker_handles_nullable_database_booleans():
    assert forecast_marker("pending", pd.NA, pd.NA).key == "insufficient"
    assert forecast_marker("matured", pd.NA, pd.NA).key == "mixed"
    assert forecast_marker("matured", True, pd.NA).key == "positive"


def test_basic_renderers_do_not_contain_known_internal_labels():
    paths = [
        Path("src/moex_analytics/dashboard/pages/news_intelligence.py"),
        Path("src/moex_analytics/dashboard/pages/market_memory.py"),
    ]
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for term in FORBIDDEN_BASIC_TERMS:
        assert term not in rendered


def test_basic_quality_page_hides_engineering_columns():
    source = Path("src/moex_analytics/dashboard/pages/human_portfolio.py").read_text(encoding="utf-8")
    quality = source.split("def render_data_quality_basic", 1)[1].split("def render_decision_flow", 1)[0]
    for internal in ("dataset_family", "recommended_action", "Historical equity universe backfill"):
        assert internal not in quality
    assert "Влияет ли это на сегодняшнее решение?" in quality
