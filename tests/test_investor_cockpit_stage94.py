from pathlib import Path

import pandas as pd

from moex_analytics.dashboard.pages.investor_cockpit import (
    conditional_projection_figure,
    projection_figure,
)


def test_cockpit_chart_separates_actual_today_bands_and_real_medoid() -> None:
    history = pd.DataFrame(
        {"trade_date": pd.to_datetime(["2026-01-29", "2026-01-30"]), "close": [99.0, 100.0]}
    )
    bands = pd.DataFrame(
        {
            "relative_session": [0, 5, 20],
            "q10_price": [100, 90, 85],
            "q25_price": [100, 95, 92],
            "median_price": [100, 102, 105],
            "q75_price": [100, 108, 112],
            "q90_price": [100, 115, 120],
        }
    )
    paths = pd.DataFrame(
        {
            "analog_date": ["2019-01-10"] * 3,
            "relative_session": [0, 5, 20],
            "projected_price": [100, 103, 108],
            "is_medoid": [True] * 3,
        }
    )
    figure = projection_figure(history, bands, paths)
    names = {trace.name for trace in figure.data}
    assert {"Фактическая цена", "Центральный исторический сценарий"} <= names
    assert any("Представительный реальный эпизод" in name for name in names)
    assert figure.data[0].x[-1] == 0
    scenario_traces = [
        trace
        for trace in figure.data
        if trace.name not in {"Фактическая цена", "Контрольные сроки"}
    ]
    assert all(trace.y[0] == 100 for trace in scenario_traces)


def test_cockpit_has_prominent_disclaimer_and_no_probability_claim() -> None:
    text = Path("src/moex_analytics/dashboard/pages/investor_cockpit.py").read_text(encoding="utf-8")
    assert "Правая часть графика — не известное будущее" in text
    assert "не числовая вероятность" in text
    assert "Вероятность роста" not in text


def test_stage100_chart_separates_expected_plausible_and_stress() -> None:
    history = pd.DataFrame(
        {"trade_date": pd.to_datetime(["2026-08-07", "2026-08-10"]), "close": [100.0, 101.0]}
    )
    curves = pd.DataFrame(
        {
            "session": [0, 1],
            "weighted_median_price": [101.0, 102.0],
            "expected60_low": [101.0, 99.0],
            "expected60_high": [101.0, 104.0],
            "plausible80_low": [101.0, 97.0],
            "plausible80_high": [101.0, 106.0],
            "stress_low": [101.0, 90.0],
            "stress_high": [101.0, 110.0],
        }
    )
    paths = pd.DataFrame(
        {
            "analog_date": ["2020-01-01", "2020-01-01"],
            "session": [0, 1],
            "projected_price": [101, 103],
        }
    )
    figure = conditional_projection_figure(history, curves, paths, show_raw=False)
    names = {trace.name for trace in figure.data}
    assert "Conditional weighted median path" in names
    assert "Expected 60% (не подтверждён)" in names
    assert "Plausible 80% (не подтверждён)" in names
    assert "Stress envelope" in names
    assert not any(name.startswith("Observed branch") for name in names)
