import duckdb

from moex_analytics.training_quality.panel import HORIZON_MINIMUM, LIQUIDITY_MINIMUM_RUB
from moex_analytics.training_quality.schema import DDL


def test_stage32_policy_is_methodological_and_schema_is_frozen():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    assert HORIZON_MINIMUM == {5: 252, 20: 504, 60: 756, 120: 1000, 250: 1500}
    assert LIQUIDITY_MINIMUM_RUB == 100_000
    columns = {row[0] for row in con.execute("DESCRIBE historical_training_panel").fetchall()}
    assert {"dataset_version", "issuer_group", "quality_tier", "rank_120"} <= columns
    assert "production" not in " ".join(row[0] for row in con.execute("SHOW TABLES").fetchall())
