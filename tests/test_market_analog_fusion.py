from datetime import date, timedelta

import duckdb

import moex_analytics.market_analog_fusion.core as fusion
from moex_analytics.market_analog_fusion.core import VERSION


def test_market_analog_fusion_is_a_separate_research_version() -> None:
    assert VERSION == "stage76-v1"


def test_fusion_uses_expanding_train_only_oos_and_is_idempotent(monkeypatch) -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE analog_trajectory_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("INSERT INTO analog_trajectory_runs VALUES ('analog','completed',now())")
    con.execute(
        "CREATE TABLE analog_oos_replays(run_id VARCHAR,secid VARCHAR,horizon INTEGER,"
        "cutoff DATE,forecast_median_return DOUBLE,actual_return DOUBLE,train_only BOOLEAN)"
    )
    con.execute("CREATE TABLE whole_market_state_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("INSERT INTO whole_market_state_runs VALUES ('market',now())")
    con.execute("CREATE TABLE whole_market_state_daily(run_id VARCHAR,trade_date DATE,return_20 DOUBLE)")
    con.execute("CREATE TABLE sector_rotation_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("INSERT INTO sector_rotation_runs VALUES ('sector',now())")
    con.execute(
        "CREATE TABLE sector_rotation_scores(run_id VARCHAR,trade_date DATE,sector VARCHAR,"
        "horizon INTEGER,momentum_score DOUBLE)"
    )
    start = date(2020, 1, 1)
    analog_rows, market_rows, sector_rows = [], [], []
    for index in range(80):
        cutoff = start + timedelta(days=index)
        prediction = (index % 9 - 4) / 100
        actual = prediction * 0.7 + (index % 3 - 1) / 200
        analog_rows.append(("analog", "AAA", 5, cutoff, prediction, actual, True))
        market_rows.append(("market", cutoff, (index % 7 - 3) / 100))
        sector_rows.append(("sector", cutoff, "TEST", 5, (index % 5 - 2) / 100))
    con.executemany("INSERT INTO analog_oos_replays VALUES (?,?,?,?,?,?,?)", analog_rows)
    con.executemany("INSERT INTO whole_market_state_daily VALUES (?,?,?)", market_rows)
    con.executemany("INSERT INTO sector_rotation_scores VALUES (?,?,?,?,?)", sector_rows)
    monkeypatch.setattr(fusion, "SECTOR_MAP", {"AAA": "TEST"})
    first = fusion.run_market_analog_fusion(con)
    second = fusion.run_market_analog_fusion(con)
    assert first["scorecards"] == 1 and not first["idempotent"]
    assert second["scorecards"] == 1 and second["idempotent"]
    assert con.execute("SELECT count(*) FROM market_analog_fusion_oos").fetchone()[0] > 0
    assert con.execute("SELECT bool_and(train_end < cutoff) FROM market_analog_fusion_oos").fetchone()[0]
    assert con.execute("SELECT bool_and(production_unchanged) FROM market_analog_fusion_runs").fetchone()[0]
