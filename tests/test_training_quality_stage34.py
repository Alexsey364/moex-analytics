import duckdb

from moex_analytics.training_quality.expansion import _queue
from moex_analytics.training_quality.schema import DDL


def test_promotion_queue_preserves_thresholds_and_exposes_missing_evidence():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.execute("""INSERT INTO corporate_action_candidate_episodes
        SELECT 'e'||i,'AAA',DATE '2020-01-01'+i::INTEGER,DATE '2020-01-01'+i::INTEGER,
        'P2',1,'[]',1,10,10,10,0,'ratio_candidate','no_official_evidence',
        'manual_review_required',current_timestamp FROM range(6) t(i)""")
    con.execute("""INSERT INTO historical_quality_v2 VALUES
        ('AAA','P2',5,1000,1,0,1,1,1,0,0.8,0,80,'C',
        'corporate_action_uncertainty','historical-quality-v2.0',current_timestamp)""")
    selected = _queue(con, "run")
    assert selected == [("AAA", ["corporate_action_uncertainty"])]
    row = con.execute("""SELECT current_tier,target_tier,missing_evidence_json
        FROM quality_promotion_queue""").fetchone()
    assert row[0:2] == ("C", "B")
    assert "official_split" in row[2]
