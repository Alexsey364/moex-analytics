from moex_analytics.whole_market_live.core import MARKET_HORIZONS, RANK_HORIZONS


def test_live_stream_contract_has_separate_horizons_and_no_probability() -> None:
    assert MARKET_HORIZONS == (1, 5, 20, 60, 120)
    assert RANK_HORIZONS == (5, 20, 60, 120, 250)
