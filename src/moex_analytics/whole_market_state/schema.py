"""DuckDB schema for immutable Stage 71 research snapshots."""

DDL = """
CREATE TABLE IF NOT EXISTS whole_market_state_runs(
    run_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP,
    cutoff DATE,
    date_from DATE,
    date_to DATE,
    observations BIGINT,
    feature_count INTEGER,
    input_hash VARCHAR,
    methodology_version VARCHAR,
    immutable BOOLEAN,
    status VARCHAR,
    details_json JSON
);

CREATE TABLE IF NOT EXISTS whole_market_state_daily(
    run_id VARCHAR,
    trade_date DATE,
    available_from TIMESTAMPTZ,
    imoex_close DOUBLE,
    return_1 DOUBLE,
    return_5 DOUBLE,
    return_20 DOUBLE,
    return_60 DOUBLE,
    return_120 DOUBLE,
    return_250 DOUBLE,
    drawdown DOUBLE,
    distance_sma20 DOUBLE,
    distance_sma50 DOUBLE,
    distance_sma100 DOUBLE,
    distance_sma200 DOUBLE,
    realized_vol20 DOUBLE,
    realized_vol60 DOUBLE,
    range_expansion DOUBLE,
    rtsi_return_20 DOUBLE,
    breadth_json JSON,
    liquidity_json JSON,
    volatility_json JSON,
    rates_json JSON,
    fx_json JSON,
    commodities_json JSON,
    sectors_json JSON,
    futures_json JSON,
    options_json JSON,
    news_json JSON,
    regime_json JSON,
    market_state_label VARCHAR,
    methodology_version VARCHAR,
    immutable BOOLEAN,
    PRIMARY KEY(run_id, trade_date)
);
"""


def ensure_schema(con: object) -> None:
    """Create Stage 71 tables without altering legacy production tables."""
    con.execute(DDL)  # type: ignore[attr-defined]
