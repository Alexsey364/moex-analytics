from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from moex_analytics.database import SCHEMA
from moex_analytics.sber_intelligence.classifier import classify
from moex_analytics.sber_intelligence.deduplication import canonical_key, choose_primary, first_confirmed_time
from moex_analytics.sber_intelligence.event_study import summarize
from moex_analytics.sber_intelligence.expectations import calculate_all
from moex_analytics.sber_intelligence.impact import build_impacts, proposed_adjustment, write_change_log
from moex_analytics.sber_intelligence.loader import update
from moex_analytics.sber_intelligence.point_in_time import anchor_session, available_as_of
from moex_analytics.sber_intelligence.quality import confounding_status
from moex_analytics.sber_intelligence.quality import run as quality_check
from moex_analytics.sber_intelligence.reaction import reaction
from moex_analytics.sber_intelligence.relevance import assess
from moex_analytics.sber_intelligence.surprises import calculate
from moex_analytics.sber_intelligence.validation import may_auto_apply, validate


@pytest.fixture
def con():
    db = duckdb.connect(":memory:")
    db.execute(SCHEMA)
    yield db
    db.close()


def test_point_in_time_and_no_future_data():
    now = datetime(2024, 1, 2, tzinfo=UTC)
    rows = [{"available_from": now - timedelta(days=1)}, {"available_from": now + timedelta(days=1)}]
    assert len(available_as_of(rows, now)) == 1


def test_deduplication_and_first_official_time():
    t = datetime(2024, 1, 1, tzinfo=UTC)
    copies = [
        {"source_id": "secondary", "available_from": t, "official_status": "secondary"},
        {"source_id": "cbr", "available_from": t + timedelta(hours=1), "official_status": "official"},
    ]
    assert canonical_key("SBER", "financial", t) == canonical_key("SBER", "financial", t)
    assert choose_primary(copies, {"cbr": 1, "secondary": 0.5})["source_id"] == "cbr"
    assert first_confirmed_time(copies) == t + timedelta(hours=1)


def test_classification_and_relevance_are_explainable():
    assert classify("RAS annual income") == ("financial", "ras_results", "document_type:RAS annual income")
    assert assess("SBER", "financial", True)[0] == 1


def test_consensus_and_no_fake_consensus():
    from moex_analytics.sber_intelligence.expectations import consensus

    assert consensus([])["value"] is None
    assert consensus([10, 20, 30])["value"] == 20


def test_surprise_with_and_without_consensus():
    assert calculate(120, None, 0)["difference"] is None
    result = calculate(120, 100, 5, 10)
    assert result["percentage"] == pytest.approx(0.2)
    assert result["standardized"] == 2


def test_publication_session_rules():
    assert anchor_session(9) == "before_open"
    assert anchor_session(12) == "during_session"
    assert anchor_session(20) == "after_close"


def test_abnormal_return_and_missing_sector():
    start = date(2024, 1, 1)
    prices = [(start + timedelta(days=i), 100 + i * 2) for i in range(10)]
    market = {d: 100 + i for i, (d, _) in enumerate(prices)}
    rows = reaction(prices, market, datetime(2024, 1, 2, 12, tzinfo=UTC))
    one = next(x for x in rows if x["window"] == "3d")
    assert one["abnormal"] == pytest.approx(one["raw"] - one["imoex"])


def test_confounded_events():
    assert confounding_status(2, 0.05, False)[0] == "heavily_confounded"
    assert confounding_status(1, 0, False)[0] == "clean_event"


def test_event_group_statistics():
    assert summarize([-0.1, 0, 0.1, 0.2])["sample_size"] == 4
    assert summarize([])["quality"] == "insufficient_data"


def test_adjustment_policy():
    text = proposed_adjustment("roe", 0.2, 0.21, "text hypothesis", False)
    assert text["requires_manual_confirmation"]
    numeric = proposed_adjustment("roe", 0.2, 0.21, "validated official number", True)
    assert numeric["status"] == "applicable"
    assert may_auto_apply("financial", True, True)
    assert not may_auto_apply("financial", True, False)


def test_validation_requires_official_point_in_time():
    status, issues = validate(
        {
            "official_status": "official",
            "available_from": datetime.now(UTC),
            "source_url": "https://cbr.ru",
            "point_in_time_safe": True,
        }
    )
    assert status == "validated" and not issues


def test_change_log(con):
    assert write_change_log(con, "a", "b", "e", "watch", "watch", [])["written"] == 0
    assert write_change_log(con, "a", "b", "e", "watch", "buy", ["profit"])["written"] == 1


def _seed(con):
    start = date(2023, 1, 1)
    prices = []
    for secid, base in (("SBER", 100.0), ("IMOEX", 2000.0)):
        for i in range(150):
            prices.append(
                (
                    start + timedelta(days=i),
                    secid,
                    secid,
                    "TQBR",
                    base + i,
                    base + i + 1,
                    base + i - 1,
                    base + i,
                    base + i,
                    1000,
                    1000,
                    10,
                    1,
                    datetime.now(),
                )
            )
    con.executemany("INSERT INTO canonical_daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", prices)
    available = datetime(2023, 2, 1, 10, tzinfo=UTC)
    con.execute(
        """INSERT INTO fundamental_documents VALUES ('doc','SBER','RAS annual income','RAS',DATE '2022-01-01',DATE '2022-12-31',DATE '2023-01-31',?,'Official income','https://cbr.ru/doc',NULL,'h','text/html','p','parsed','validated','original',current_timestamp,NULL)""",
        [available],
    )
    con.execute(
        """INSERT INTO macro_observations VALUES ('cbr_key_rate',DATE '2023-02-01',DATE '2023-02-01',?,7.5,'original',current_timestamp,'Bank of Russia')""",
        [available],
    )


def test_integrated_pipeline_no_change_and_live_state(con):
    _seed(con)
    first = update(con)
    assert first["status"] == "success"
    assert first["sber_events"] == 2
    assert first["sber_event_reactions"] > 0
    assert first["live"]["information_confidence"] > 0
    assert update(con)["status"] == "no_change"
    assert con.execute("SELECT count(*) FROM sber_event_studies").fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM sber_surprises WHERE consensus IS NULL").fetchone()[0] == 1
    assert (
        con.execute("SELECT count(*) FROM sber_event_reactions WHERE finance_return IS NULL").fetchone()[0]
        > 0
    )


def test_expectation_point_in_time_and_surprise(con):
    _seed(con)
    update(con)
    con.execute(
        "INSERT INTO sber_expectations VALUES ('f','publisher',DATE '2023-01-01',TIMESTAMPTZ '2023-01-01 10:00:00+00',DATE '2023-02-01','key_rate',7,NULL,NULL,'percent','url','doc',3,'median',50,'validated')"
    )
    result = calculate_all(con)
    assert result["validated_expectations"] == 1
    assert con.execute("SELECT difference FROM sber_surprises WHERE metric_id='key_rate'").fetchone()[
        0
    ] == pytest.approx(0.5)


def test_quality_and_impacts_weight_zero(con):
    _seed(con)
    update(con)
    result = build_impacts(con)
    assert result["auto_apply_allowed"] == 0
    assert (
        con.execute(
            "SELECT count(*) FROM sber_event_impacts WHERE impact_status='informational_only'"
        ).fetchone()[0]
        == 2
    )
    assert quality_check(con)["issues"] == 0


def test_partial_dashboard_import():
    from moex_analytics.dashboard.pages import sber_intelligence

    assert all(
        callable(getattr(sber_intelligence, name))
        for name in (
            "render_feed",
            "render_calendar",
            "render_reactions",
            "render_expectations",
            "render_changes",
            "render_operational",
            "render_quality",
        )
    )
