from moex_analytics.config import load_instruments, load_settings


def test_initial_instrument_universe() -> None:
    tickers = [item["ticker"] for item in load_instruments()]
    assert tickers == ["IMOEX", "SBER", "LKOH", "GAZP"]


def test_settings_have_database_path() -> None:
    assert load_settings()["paths"]["database"] == "database/market.duckdb"
