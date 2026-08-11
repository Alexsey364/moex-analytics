from datetime import UTC, datetime

import duckdb

from moex_analytics.news_foundation.core import load_source_registry
from moex_analytics.news_intelligence.core import (
    classify_event,
    ensure_schema,
    extract_entities,
    ingest_live_news,
    ingest_records,
    parse_rss,
)


def _record(news_id: str, source: str = "cbr_press_rss") -> tuple[str, dict]:
    published = datetime(2026, 8, 10, 9, tzinfo=UTC)
    return source, {"news_id": news_id, "published_at": published,
        "headline": "Bank of Russia key rate decision", "summary": "Rate unchanged",
        "url": f"https://example.test/{news_id}", "content_hash": news_id * 8,
        "language": "en"}


def test_classification_and_rss_are_descriptive_and_pit_safe():
    retrieved = datetime(2026, 8, 10, 10, tzinfo=UTC)
    rss = b"""<rss><channel><item><title>Bank of Russia key rate decision</title>
      <link>https://example.test/1</link><description><![CDATA[<b>Rate unchanged</b>]]></description>
      <pubDate>Mon, 10 Aug 2026 09:00:00 GMT</pubDate></item></channel></rss>"""
    rows = parse_rss(rss, "cbr_press_rss", retrieved)
    assert rows[0]["available_from"] == retrieved
    assert rows[0]["body"] is None
    assert "<b>" not in rows[0]["summary"]
    assert classify_event(rows[0]["headline"]) == "central_bank"
    assert {"Russia", "CBR"}.issubset(extract_entities(rows[0]["headline"]))


def test_story_clustering_provenance_scheduled_and_idempotency():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    retrieved = datetime(2026, 8, 10, 10, tzinfo=UTC)
    source, first = _record("a")
    first["scheduled_at"] = datetime(2026, 8, 12, 10, tzinfo=UTC)
    assert ingest_records(con, source, [first], retrieved)["inserted"] == 1
    assert ingest_records(con, source, [first], retrieved)["inserted"] == 0
    second_source, second = _record("b", "fed_press_rss")
    second["headline"] = "Bank of Russia publishes an update to its key rate decision"
    assert ingest_records(con, second_source, [second], retrieved)["stories_updated"] == 1
    row = con.execute("SELECT item_count,source_count,status,reliability FROM news_stories").fetchone()
    assert row == (2, 2, "developing", "multi_source_official")
    assert con.execute("SELECT scheduled,body,immutable FROM news_items WHERE news_id='a'").fetchone() == (
        True, None, True)
    assert con.execute("SELECT count(*) FROM news_provenance").fetchone()[0] == 2


class _Response:
    content = (b"<rss><channel><item><title>Oil market update</title>"
               b"<link>https://example.test/oil</link></item></channel></rss>")

    def raise_for_status(self):
        return None


class _Session:
    def get(self, *args, **kwargs):
        return _Response()


def test_live_run_uses_registry_and_persists_monitoring():
    con = duckdb.connect(":memory:")
    load_source_registry(con)
    result = ingest_live_news(con, _Session())
    assert result["sources"] == 5
    assert result["errors"] == 0
    assert result["inserted"] == 5
    assert con.execute("SELECT status FROM news_ingestion_runs").fetchone()[0] == "completed"
