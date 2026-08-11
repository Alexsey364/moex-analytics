import duckdb

from moex_analytics.dashboard.pages.news_intelligence import load_news_view
from moex_analytics.news_intelligence.core import ensure_schema as ensure_news
from moex_analytics.news_reaction.core import ensure_schema as ensure_reactions
from moex_analytics.news_research.core import ensure_schema as ensure_research


def test_news_dashboard_has_graceful_empty_state():
    con = duckdb.connect(":memory:")
    ensure_news(con)
    ensure_reactions(con)
    ensure_research(con)
    assert load_news_view(con) == {"items": [], "reactions": [], "research": None}


def test_news_dashboard_reads_immutable_research_without_recalculation():
    con = duckdb.connect(":memory:")
    ensure_news(con)
    ensure_reactions(con)
    ensure_research(con)
    con.execute("INSERT INTO news_items (news_id,published_at,headline,event_type,entities_json,tone,"
                "immutable) VALUES ('n',current_timestamp,'Official update','rates','[]','uncertain',true)")
    con.execute("INSERT INTO news_research_runs VALUES ('r',DATE '2026-08-10','requires_more_history',"
                "0,0,0,current_timestamp,'gated')")
    view = load_news_view(con)
    assert len(view["items"]) == 1
    assert view["research"] == ("requires_more_history", 0, 0, 0.0)
