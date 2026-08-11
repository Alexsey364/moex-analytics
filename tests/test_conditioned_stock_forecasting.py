import duckdb

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS, SECTOR_MAP
from moex_analytics.conditioned_stock_forecasting.schema import ensure_schema


def test_portfolio_universe_and_ablation_contract() -> None:
    assert set(SECIDS) == {"X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX"}
    assert set(SECTOR_MAP) == set(SECIDS)
    assert HORIZONS == (5, 20, 60, 120, 250)


def test_oos_error_schema_defines_gain_and_stability_evidence() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    score_columns = {
        row[0] for row in con.execute("DESCRIBE conditioned_stock_scorecards").fetchall()
    }
    oos_columns = {row[0] for row in con.execute("DESCRIBE conditioned_stock_oos").fetchall()}
    assert {"ci_low", "ci_high", "fold_stable"} <= score_columns
    assert {
        "baseline_absolute_error",
        "candidate_absolute_error",
        "mae_gain",
        "trade_date",
    } <= oos_columns
    ensure_schema(con)
