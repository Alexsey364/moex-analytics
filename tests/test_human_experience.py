from pathlib import Path

import pandas as pd
import pytest

from moex_analytics.dashboard.human_experience import (
    FORBIDDEN_BASIC_TERMS,
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
