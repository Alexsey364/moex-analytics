from datetime import date, timedelta

import duckdb

from moex_analytics.sector_rotation import run_sector_rotation_research


def test_sector_rotation_is_cross_sectional_frozen_and_idempotent() -> None:
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE macro_observations(series_id VARCHAR,observation_date DATE,
    release_date DATE,available_from TIMESTAMPTZ,value DOUBLE,vintage VARCHAR,
    loaded_at TIMESTAMP,source VARCHAR)""")
    series = ["moex_imoex", "moex_chemicals", "moex_consumer", "moex_finance", "moex_metals"]
    rows = []
    start = date(2015, 1, 1)
    for i in range(700):
        day = start + timedelta(days=i)
        for j, name in enumerate(series):
            rows.append((name, day, day, day, 1000 + i * (j + 1) / 10 + (i % 13) * j, "v", day, "test"))
    con.executemany("INSERT INTO macro_observations VALUES (?,?,?,?,?,?,?,?)", rows)
    first = run_sector_rotation_research(con)
    second = run_sector_rotation_research(con)
    assert first["sectors"] == 4
    assert first["horizons"] == 5
    assert second["idempotent"] is True
    samples = {r[0] for r in con.execute("select distinct sample from sector_rotation_scores").fetchall()}
    assert samples == {"validation", "frozen_holdout"}
