from moex_analytics.market_analog_fusion.core import VERSION


def test_market_analog_fusion_is_a_separate_research_version() -> None:
    assert VERSION == "stage76-v1"
