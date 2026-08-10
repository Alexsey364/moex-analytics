import duckdb

from moex_analytics.live_ranking import core


def _database():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE ranking_research_runs(run_id VARCHAR,cutoff DATE,dataset_version "
                "VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE rank_group_runs(run_id VARCHAR,status VARCHAR,created_at TIMESTAMP)")
    con.execute("CREATE TABLE current_rank_groups(run_id VARCHAR,secid VARCHAR,horizon INTEGER,"
                "rank_estimate DOUBLE,group_label VARCHAR)")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,canonical_secid VARCHAR,close DOUBLE)")
    con.execute("INSERT INTO ranking_research_runs VALUES "
                "('rank','2026-01-02','model-v1','completed',current_timestamp)")
    con.execute("INSERT INTO rank_group_runs VALUES ('groups','completed',current_timestamp)")
    for horizon in (60, 120, 250):
        for secid, rank in (("A", .8), ("B", .2)):
            con.execute("INSERT INTO current_rank_groups VALUES ('groups',?,?,?,?)",
                        [secid, horizon, rank, "TOP GROUP" if rank > .5 else "BOTTOM GROUP"])
    return con


def test_live_snapshots_are_prospective_immutable_and_idempotent():
    con = _database()
    first = core.update_live_rankings(con)
    assert first["created"] == 6
    assert first["pending"] == 6
    assert first["retrospective_reconstruction"] is False
    second = core.update_live_rankings(con)
    assert second["created"] == 0
    assert con.execute("SELECT count(distinct snapshot_id) FROM live_ranking_snapshots").fetchone()[0] == 6


def test_only_mature_outcomes_are_evaluated():
    con = _database()
    for day in range(1, 70):
        date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?)", [date, "A", 100 + day])
        con.execute("INSERT INTO canonical_daily_prices VALUES (?,?,?)", [date, "B", 100 - day / 2])
    result = core.update_live_rankings(con)
    assert result["matured"] == 2
    assert result["pending"] == 4
