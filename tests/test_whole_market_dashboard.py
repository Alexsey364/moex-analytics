from moex_analytics.dashboard.pages.whole_market import _direction, market_reasons


def test_market_reasons_are_bounded_and_explainable() -> None:
    positive, caution = market_reasons({"return_20": 0.1, "drawdown": -0.2, "realized_vol20": 0.4})
    assert len(positive) <= 3 and len(caution) <= 3
    assert any("20 сессий" in value for value in positive)
    assert any("Просадка" in value for value in caution)


def test_fusion_direction_vocabulary_is_translated() -> None:
    assert "положительно" in _direction("positive")
    assert "отрицательно" in _direction("negative")
