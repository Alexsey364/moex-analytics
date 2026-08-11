from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS, SECTOR_MAP


def test_portfolio_universe_and_ablation_contract() -> None:
    assert set(SECIDS) == {"X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX"}
    assert set(SECTOR_MAP) == set(SECIDS)
    assert HORIZONS == (5, 20, 60, 120, 250)
