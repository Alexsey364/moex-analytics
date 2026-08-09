from datetime import date

import duckdb

from moex_analytics.database import SCHEMA
from moex_analytics.portfolio_research.intelligence import DDL as INTELLIGENCE_DDL
from moex_analytics.predictive_expansion.fundamentals import (
    deepen_pit_fundamentals,
    fundamental_status,
)


def test_fundamental_coverage_and_pit_dividend_are_truthful():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    con.execute(INTELLIGENCE_DDL)
    con.execute(
        """INSERT INTO issuer_fundamental_values
        (secid,metric,reporting_standard,period_end,publication_date,available_from,
        validation_status,revision) VALUES
        ('LKOH','revenue','IFRS','2024-12-31','2025-03-01','2025-03-02','validated','original')"""
    )
    con.execute(
        "INSERT INTO daily_prices(trade_date,secid,board,close,source) "
        "VALUES ('2024-06-03','LKOH','TQBR',7000,'test')"
    )
    con.execute(
        """INSERT INTO dividends VALUES
        ('LKOH','2024-06-20','2024-06-01','2024-07-01',500,'RUB','official',current_timestamp,'x')"""
    )

    result = deepen_pit_fundamentals(con, download=False)

    assert result["production_changes"] == 0
    assert result["validated_periods"] == 1
    assert result["dividends"] == 1
    lkoh = next(row for row in result["coverage"] if row["issuer"] == "LKOH")
    assert lkoh["status"] == "insufficient_sample"
    pit = con.execute(
        "SELECT publication_date,dividend_yield_pit,quality_status FROM stage30_dividend_pit"
    ).fetchone()
    assert pit[0] == date(2024, 6, 1)
    assert round(pit[1], 6) == round(500 / 7000, 6)
    assert pit[2] == "pit_valid"
    assert fundamental_status(con)["latest"][1] == "completed_with_source_gaps"
