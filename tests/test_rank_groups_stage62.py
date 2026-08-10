import duckdb
import pandas as pd

from moex_analytics.rank_groups import core


def test_overlap_groups_are_transitive_and_bootstrap_is_deterministic():
    frame = pd.DataFrame({"secid": ["A", "B", "C"], "rank_low": [.7, .6, .1],
                          "rank_high": [.9, .75, .3]})
    groups = core._overlap_groups(frame)
    assert groups["A"] == groups["B"]
    assert groups["C"] != groups["A"]
    row = type("Row", (), {"rank_low": .7, "rank_high": .9,
                            "relative_rank": .8, "secid": "A"})()
    assert core._bootstrap_frequencies(row, 60) == core._bootstrap_frequencies(row, 60)


def test_rank_group_run_is_immutable_decomposed_and_probability_safe():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE ranking_research_runs(run_id VARCHAR,cutoff DATE,status VARCHAR,"
                "finished_at TIMESTAMP)")
    con.execute("CREATE TABLE long_horizon_ranking_runs(run_id VARCHAR,status VARCHAR,finished_at TIMESTAMP)")
    con.execute("CREATE TABLE current_portfolio_ranking(run_id VARCHAR,cutoff DATE,secid VARCHAR,"
                "horizon INTEGER,relative_rank DOUBLE,rank_low DOUBLE,rank_high DOUBLE,tie_group INTEGER,"
                "model_agreement DOUBLE,historical_oos DOUBLE,live_evidence VARCHAR,status VARCHAR,"
                "reason VARCHAR,immutable BOOLEAN)")
    con.execute("CREATE TABLE long_horizon_ranking_validation(run_id VARCHAR,horizon INTEGER,"
                "context_type VARCHAR,rank_ic DOUBLE,top_bottom_spread_after_costs DOUBLE,status VARCHAR)")
    con.execute("INSERT INTO ranking_research_runs VALUES "
                "('rank','2026-08-07','completed',current_timestamp)")
    con.execute("INSERT INTO long_horizon_ranking_runs VALUES ('validation','completed',current_timestamp)")
    for horizon in core.HORIZONS:
        con.execute("INSERT INTO long_horizon_ranking_validation VALUES "
                    "('validation',?,'all',.12,.04,'ROBUST_RELATIVE_EDGE')", [horizon])
        for secid, rank in (("A", .9), ("B", .5), ("C", .1)):
            con.execute("INSERT INTO current_portfolio_ranking VALUES "
                        "('rank','2026-08-07',?,?,?,?-.05,?+.05,1,.9,.12,'pending','research','x',true)",
                        [secid, horizon, rank, rank, rank])
    result = core.run_rank_grouping(con)
    assert result["rows"] == 9
    assert core.run_rank_grouping(con)["cached"] is True
    assert con.execute("SELECT count(*) FROM composite_rank_groups").fetchone()[0] == 3
    details = con.execute("SELECT details_json FROM rank_group_runs").fetchone()[0]
    assert '"probability_published": false' in details
