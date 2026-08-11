from datetime import date, timedelta
from math import sin

import duckdb

from moex_analytics.market_forecasting import run_market_forecast_research
from moex_analytics.market_forecasting.schema import ensure_schema
from moex_analytics.whole_market_state.schema import ensure_schema as ensure_state


def _db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    ensure_state(con)
    ensure_schema(con)
    run = "state"
    con.execute(
        "INSERT INTO whole_market_state_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            run,
            date(2025, 1, 1),
            date(2025, 1, 1),
            date(2020, 1, 1),
            date(2025, 1, 1),
            700,
            28,
            "x",
            "v",
            True,
            "completed",
            "{}",
        ],
    )
    columns = [row[0] for row in con.execute("DESCRIBE whole_market_state_daily").fetchall()]
    rows = []
    for i in range(700):
        values = {name: None for name in columns}
        values.update(
            run_id=run,
            trade_date=date(2020, 1, 1) + timedelta(days=i),
            imoex_close=2000 + i * 0.2 + 50 * sin(i / 8),
            available_from=date(2020, 1, 1) + timedelta(days=i),
            methodology_version="v",
            immutable=True,
        )
        for name in columns:
            if (
                name.startswith("return_")
                or name.startswith("distance_")
                or name
                in {"drawdown", "realized_vol20", "realized_vol60", "range_expansion", "rtsi_return_20"}
            ):
                values[name] = (i % 17 - 8) / 100
            elif name.endswith("_json"):
                values[name] = "{}"
        rows.append([values[name] for name in columns])
    names = ",".join(columns)
    placeholders = ",".join("?" for _ in columns)
    con.executemany(f"INSERT INTO whole_market_state_daily ({names}) VALUES ({placeholders})", rows)
    return con


def test_chronological_forecast_is_immutable_and_probability_gated() -> None:
    con = _db()
    first = run_market_forecast_research(con)
    second = run_market_forecast_research(con)
    assert first["horizons"] == 5
    assert first["models"] == 4
    assert second["idempotent"] is True
    assert (
        con.execute(
            "SELECT count(*) FROM market_forecast_predictions WHERE probability_published"
        ).fetchone()[0]
        == 0
    )
    split = con.execute(
        "SELECT frozen_train_to,frozen_validation_to,holdout_from FROM market_forecast_runs"
    ).fetchone()
    assert split[0] < split[1] < split[2]
