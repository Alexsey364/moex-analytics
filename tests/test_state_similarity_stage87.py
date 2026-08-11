from datetime import date

import duckdb
import numpy as np
import pandas as pd

import moex_analytics.state_similarity.core as state


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)")
    con.execute("CREATE TABLE whole_market_state_runs(run_id VARCHAR,cutoff DATE,status VARCHAR)")
    con.execute(
        "CREATE TABLE whole_market_state_daily(run_id VARCHAR,trade_date DATE,imoex_close DOUBLE,"
        "return_20 DOUBLE,drawdown DOUBLE,realized_vol20 DOUBLE)"
    )
    rng = np.random.default_rng(87)
    dates = pd.bdate_range(date(2018, 1, 1), periods=700)
    stock = 100 * np.cumprod(1 + rng.normal(0.0003, 0.012, len(dates)))
    market = 1000 * np.cumprod(1 + rng.normal(0.0002, 0.009, len(dates)))
    con.executemany(
        "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
        [("AAA", day.date(), float(value)) for day, value in zip(dates, stock, strict=True)],
    )
    market_frame = pd.DataFrame({"date": dates, "close": market})
    market_frame["ret20"] = market_frame.close.pct_change(20)
    market_frame["dd"] = market_frame.close / market_frame.close.cummax() - 1
    market_frame["vol"] = market_frame.close.pct_change().rolling(20).std() * np.sqrt(252)
    rows = [
        ("market", row.date.date(), float(row.close), float(row.ret20), float(row.dd), float(row.vol))
        for row in market_frame.dropna().itertuples()
    ]
    con.executemany("INSERT INTO whole_market_state_daily VALUES (?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO whole_market_state_runs VALUES ('market',?,'completed')", [dates[-1].date()])
    return con


def test_state_similarity_is_pit_only_real_outcomes_and_idempotent(monkeypatch) -> None:
    con = _con()
    monkeypatch.setattr(state, "SECIDS", ("AAA",))
    monkeypatch.setattr(state, "HORIZONS", (5, 20))
    first = state.run_state_similarity(con)
    second = state.run_state_similarity(con)
    assert first["matches"] > 0 and first["validations"] == 6
    assert not first["idempotent"] and second["idempotent"]
    assert set(
        row[0] for row in con.execute("SELECT DISTINCT analog_type FROM state_similarity_matches").fetchall()
    ) == {"path", "state", "combined"}
    assert con.execute(
        "SELECT bool_and(analog_date<=history_end AND independent AND immutable) "
        "FROM state_similarity_matches"
    ).fetchone()[0]
    assert con.execute(
        "SELECT bool_and(observed_until<=?) FROM state_similarity_outcomes", [first["cutoff"]]
    ).fetchone()[0]
    assert con.execute(
        "SELECT bool_and(train_only AND immutable) FROM state_similarity_validation"
    ).fetchone()[0]
    assert not con.execute(
        "SELECT bool_or(combined_weight_allowed) FROM state_similarity_validation "
        "WHERE analog_type<>'combined'"
    ).fetchone()[0]


def test_combined_distance_uses_only_common_historical_dates() -> None:
    left = pd.Series([0.1, 0.2], index=pd.to_datetime(["2020-01-01", "2020-02-01"]))
    right = pd.Series([0.3, 0.1], index=pd.to_datetime(["2020-02-01", "2020-03-01"]))
    combined = state._combined(left, right)
    assert list(combined.index) == [pd.Timestamp("2020-02-01")]
    assert state._combined(pd.Series(dtype=float), right).empty
