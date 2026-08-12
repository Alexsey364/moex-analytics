from moex_analytics.dashboard.navigation import group_advanced_pages
from moex_analytics.dashboard.pages import decision_forecast_cockpit


def test_renderer_is_registered_and_callable() -> None:
    assert callable(decision_forecast_cockpit.render)
    assert callable(decision_forecast_cockpit.load_return_forecast)
    grouped = group_advanced_pages({"Decision Forecast Cockpit": decision_forecast_cockpit.render})
    assert "Decision Forecast Cockpit" in grouped["Модели"]
