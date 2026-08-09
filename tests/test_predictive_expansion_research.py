import duckdb

from moex_analytics.actual_backfill.schema import DDL as MARKET_DDL
from moex_analytics.predictive_expansion.research import (
    build_cross_sectional_dataset,
    measure_data_value,
)


def test_cross_section_is_gated_before_required_universe():
    con = duckdb.connect(":memory:")
    con.execute(MARKET_DDL)
    result = build_cross_sectional_dataset(con, minimum_securities=2)
    assert result["status"] == "gated"
    assert result["available"] == 0
    assert result["production_changes"] == 0


def test_data_value_does_not_confuse_availability_with_oos_value():
    con = duckdb.connect(":memory:")
    con.execute(MARKET_DDL)
    result = measure_data_value(con)
    assert result["useful"] == 0
    assert result["insufficient_sample"] == 12
    assert all(row["oos_effect"] is None for row in result["families"])
    assert result["production_changes"] == 0
