from datetime import date, timedelta

import duckdb

from moex_analytics.news_research.core import run_news_research


def _schema(con):
    con.execute("CREATE TABLE news_items(news_id VARCHAR,tone VARCHAR)")
    con.execute("CREATE TABLE news_reaction_memory(news_id VARCHAR,horizon INTEGER,"
                "market_return DOUBLE,anchor_date DATE)")


def test_empty_realistic_history_is_gated_not_invented():
    con = duckdb.connect(":memory:")
    _schema(con)
    result = run_news_research(con)
    assert result["status"] == "requires_more_history"
    assert result["production_weight"] == 0
    assert result["probability_allowed"] is False
    assert con.execute("SELECT count(*),bool_and(status='insufficient_history') FROM "
                       "news_research_scorecards").fetchone() == (12, True)


def test_mature_history_keeps_challengers_research_only_and_temporal_split():
    con = duckdb.connect(":memory:")
    _schema(con)
    start = date(2020, 1, 1)
    for i in range(35):
        tone = "positive_wording" if i % 2 else "negative_wording"
        con.execute("INSERT INTO news_items VALUES (?,?)", [f"n{i}", tone])
        con.execute("INSERT INTO news_reaction_memory VALUES (?,?,?,?)",
                    [f"n{i}", 5, 0.01 if i % 2 else -0.01, start + timedelta(days=i)])
    result = run_news_research(con, start + timedelta(days=40))
    assert result["validated_variants"] == 2
    assert result["production_weight"] == 0
    row = con.execute("SELECT train_end<oos_start,probability_allowed,production_weight FROM "
                      "news_research_scorecards WHERE horizon=5 AND variant='news_event_type'").fetchone()
    assert row == (True, False, 0.0)
