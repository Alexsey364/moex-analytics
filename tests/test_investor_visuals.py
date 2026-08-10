import pandas as pd

from moex_analytics.dashboard.investor_visuals import (
    breadth_figure,
    horizon_heatmap,
    live_progress,
    risk_weight_figure,
    scenario_figure,
    terminal_range_figure,
)
from moex_analytics.dashboard.visual_semantics import (
    TOKENS,
    accessible_label,
    color_for,
    confidence_segments,
    forecast_marker,
    theme_css,
    token_for,
)


def test_status_semantics_are_accessible_and_theme_safe():
    assert token_for("GREEN").symbol == "↑"
    assert token_for("RED").symbol == "↓"
    assert token_for("GRAY").symbol == "?"
    assert token_for("↑").key == "positive"
    assert token_for("→").key == "mixed"
    assert token_for("↓").key == "negative"
    assert "Положительный" in accessible_label("GREEN")
    assert color_for("GREEN") != color_for("GREEN", dark=True)
    assert set(TOKENS) == {"positive", "mixed", "caution", "negative", "neutral", "insufficient"}
    assert confidence_segments(0.7) == "●●●○ выше средней"
    assert "prefers-color-scheme: dark" in theme_css()


def test_forecast_marker_pending_matured_and_neutral_semantics():
    assert forecast_marker("pending", True).key == "insufficient"
    assert forecast_marker("matured", True).key == "positive"
    assert forecast_marker("matured", False).key == "negative"
    assert forecast_marker("matured", None, True).key == "mixed"


def test_horizon_heatmap_has_text_and_model_hover():
    frame = pd.DataFrame(
        [
            {
                "secid": "SBERP",
                "horizon": horizon,
                "status": "GREEN",
                "confidence": 0.7,
                "model": "saved-v1",
                "sample": 120,
            }
            for horizon in (1, 5, 20, 60, 120, 250)
        ]
    )
    figure = horizon_heatmap(frame)
    assert figure.data[0].text[0][0] == "↑"
    assert "saved-v1" in figure.data[0].customdata[0][0]


def test_terminal_ranges_do_not_create_fake_forecast_path():
    forecast = pd.Series(
        {
            "horizon_sessions": 20,
            "current_price": 100,
            "median_return": 0.05,
            "range_50_low": -0.03,
            "range_50_high": 0.08,
            "range_80_low": -0.08,
            "range_80_high": 0.12,
            "range_90_low": -0.12,
            "range_90_high": 0.18,
        }
    )
    actual = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=3), "close": [100, 101, 99]})
    figure = terminal_range_figure(forecast, actual)
    names = [trace.name for trace in figure.data]
    assert names.count("Фактическая траектория") == 1
    assert not any("forecast trajectory" in name.lower() for name in names)
    for trace in figure.data:
        if "terminal interval" in trace.name:
            assert len(set(trace.x)) == 1


def test_portfolio_breadth_scenario_and_no_fake_accuracy():
    portfolio = pd.DataFrame(
        {"secid": ["A", "B"], "equity_weight": [0.6, 0.4], "risk_contribution": [0.8, 0.2]}
    )
    risk_figure = risk_weight_figure(portfolio)
    assert len(risk_figure.data) == 2
    assert list(risk_figure.data[0].x) == [0.6, 0.4]
    assert list(risk_figure.data[1].x) == [0.8, 0.2]
    breadth = pd.DataFrame(
        {
            "trade_date": pd.date_range("2025-01-01", periods=2),
            "tradable_count": [100, 100],
            "above_sma200": [30, 35],
            "above_sma50": [40, 45],
            "advancing": [51, 49],
        }
    )
    assert len(breadth_figure(breadth).data) == 3
    scenarios = pd.DataFrame({"secid": ["A", "B"], "mechanical_sensitivity": [-0.1, 0.04]})
    assert "не прогноз" in scenario_figure(scenarios).layout.title.text
    assert live_progress(0, 0, 0, 108) is None
    assert live_progress(1, 1, 0, 10) is not None
