from moex_analytics.dashboard.pages.whole_market import _direction, market_reasons


def test_market_reasons_are_bounded_and_explainable() -> None:
    positive, caution = market_reasons({"return_20": 0.1, "drawdown": -0.2, "realized_vol20": 0.4})
    assert len(positive) <= 3 and len(caution) <= 3
    assert any("20 сессий" in value for value in positive)
    assert any("Просадка" in value for value in caution)


def test_fusion_direction_vocabulary_is_translated() -> None:
    assert "положительно" in _direction("positive")
    assert "отрицательно" in _direction("negative")


def test_stress_reasons_include_current_risk_context() -> None:
    positive, caution = market_reasons(
        {
            "return_20": 0.0596,
            "drawdown": -0.4651,
            "realized_vol20": 0.328,
            "volatility": {"rvi": 38.56},
            "rates": {"cbr_key_rate": 14.0},
        }
    )
    assert any("20 сессий" in reason for reason in positive)
    assert len(caution) == 3
    assert any("RVI" in reason for reason in caution)
