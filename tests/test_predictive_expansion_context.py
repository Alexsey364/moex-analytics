import duckdb

from moex_analytics.critical_data.schema import DDL as CRITICAL_DDL
from moex_analytics.database import SCHEMA
from moex_analytics.market_history import DDL as MARKET_DDL
from moex_analytics.predictive_expansion.context import build_validated_market_context


def test_context_preserves_pit_and_gates_unvalidated_derivatives():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    con.execute(CRITICAL_DDL)
    con.execute(MARKET_DDL)
    con.execute(
        """INSERT INTO macro_observations VALUES
        ('cbr_key_rate','2025-01-01','2025-01-01','2025-01-01',20,'current',current_timestamp,'CBR'),
        ('cbr_ruonia','2025-01-01','2025-01-02','2025-01-02',19,'current',current_timestamp,'CBR')"""
    )
    con.execute(
        """INSERT INTO sber_futures_contracts VALUES
        ('SRH5','SBER','x','2024-01-01','2025-03-01','2025-03-20',1,'unknown','MOEX')"""
    )
    con.execute(
        """INSERT INTO sber_futures_daily VALUES
        ('2025-01-01','SRH5',NULL,NULL,NULL,30000,30000,10,100,5,'MOEX','2025-01-02')"""
    )
    con.execute("INSERT INTO intraday_features(secid,trade_date) VALUES ('SBER','2025-01-01')")

    result = build_validated_market_context(con)

    assert result["production_changes"] == 0
    assert result["futures_basis_enabled"] is False
    assert con.execute("SELECT basis FROM stage30_futures_features").fetchone()[0] is None
    spread = con.execute(
        "SELECT value FROM stage30_context_features WHERE feature_name='ruonia_key_spread'"
    ).fetchone()[0]
    options = con.execute(
        "SELECT expansion_decision FROM stage30_pilot_evaluations WHERE pilot='options'"
    ).fetchone()[0]
    assert spread == -1
    assert options == "do_not_expand"
