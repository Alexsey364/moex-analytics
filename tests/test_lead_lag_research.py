from moex_analytics.lead_lag_research.core import LAGS, SECIDS, SIGNALS


def test_lead_lag_contract_is_portfolio_wide_and_temporal() -> None:
    assert len(SECIDS) == 9
    assert LAGS == (1, 5, 20)
    assert {"cbr_usd_rub", "fred_brent", "moex_rgbi", "moex_rvi"} <= set(SIGNALS)
