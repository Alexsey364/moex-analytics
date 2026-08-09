from pathlib import Path

import duckdb

from moex_analytics.actual_backfill.schema import DDL as MARKET_DDL
from moex_analytics.training_quality.corporate_actions import build_corporate_action_quality


def test_stage31_clusters_candidates_without_silent_adjustment(tmp_path: Path):
    con = duckdb.connect(":memory:")
    con.execute(MARKET_DDL)
    con.execute(
        """CREATE TABLE issuer_fundamental_values(
        secid VARCHAR,period_end DATE,validation_status VARCHAR)"""
    )
    con.execute(
        """CREATE TABLE stage30_security_quality(
        secid VARCHAR,first_trade DATE,last_trade DATE,observations BIGINT,
        history_years DOUBLE,missing_ohlc DOUBLE,volume_coverage DOUBLE,
        numtrades_coverage DOUBLE,board_count INTEGER,board_continuity VARCHAR,
        corporate_action_flags INTEGER,fundamental_periods INTEGER,pit_quality VARCHAR,
        feature_coverage DOUBLE,training_tier VARCHAR,exclusion_reason VARCHAR,
        calculated_at TIMESTAMP)"""
    )
    con.execute(
        """CREATE TABLE stage30_liquidity_daily(
        trade_date DATE,secid VARCHAR,turnover_20 DOUBLE)"""
    )
    con.execute(
        "INSERT INTO stage30_security_quality VALUES "
        "('SBER',NULL,NULL,1000,8,0,1,1,1,'single_board',2,0,'missing',1,'B',NULL,NULL)"
    )
    con.execute(
        "INSERT INTO equity_board_history VALUES "
        "('SBER','TQBR','2020-01-01','2020-01-03',3,100,TRUE,NULL,NULL)"
    )
    con.execute(
        """INSERT INTO moex_equity_eod
        (trade_date,secid,boardid,trading_session,close,value,volume,num_trades)
        VALUES ('2020-01-01','SBER','TQBR',0,100,100,1,1),
        ('2020-01-02','SBER','TQBR',0,50,100,1,1),
        ('2020-01-03','SBER','TQBR',0,100,100,1,1)"""
    )
    result = build_corporate_action_quality(con, tmp_path / "review.yaml")
    assert result["episodes"] == 1
    assert result["auto_validated"] == 0
    assert result["manual_review"] == 1
    prices = con.execute(
        "SELECT raw_price,research_adjusted_price FROM research_adjusted_prices ORDER BY trade_date"
    ).fetchall()
    assert all(raw == adjusted for raw, adjusted in prices)
    assert result["production_changes"] == 0
