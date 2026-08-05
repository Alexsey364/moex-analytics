"""Evidence-based confidence components; not a forecast probability."""

import json
from datetime import date


def score(
    *,
    key_metrics: int,
    total_key_metrics: int,
    age_days: int,
    has_ifrs: bool,
    has_ras: bool,
    consistency_checks: int,
    consistency_passed: int,
    has_shares: bool,
    history_years: float,
    validated_releases: int,
    manual_ratio: float,
    ambiguities: int,
    stable_methodology: bool,
) -> dict:
    completeness = min(100, 100 * key_metrics / max(total_key_metrics, 1))
    freshness = max(0, 100 - age_days / 7)
    evidence = min(100, validated_releases * 8 + history_years * 4)
    consistency = 100 * consistency_passed / max(consistency_checks, 1)
    source = 25 * has_ifrs + 20 * has_ras + 20 * has_shares + 35 * stable_methodology
    penalty = min(60, manual_ratio * 40 + ambiguities * 10)
    data = max(
        0,
        min(
            100,
            0.3 * completeness
            + 0.15 * freshness
            + 0.2 * evidence
            + 0.2 * consistency
            + 0.15 * source
            - penalty,
        ),
    )
    return {
        "data_confidence": round(data, 1),
        "valuation_confidence": round(data * 0.85, 1),
        "backtest_confidence": round(min(data, evidence) * 0.8, 1),
        "components": {
            "completeness": completeness,
            "freshness": freshness,
            "evidence": evidence,
            "consistency": consistency,
            "source": source,
            "penalty": penalty,
        },
    }


def calculate_current(con, version="sber-confidence-v1") -> dict:
    latest = con.execute(
        "SELECT max(period_end),count(DISTINCT document_id) FROM fundamental_metric_values WHERE quality_status='validated'"
    ).fetchone()
    if not latest[0]:
        return {"status": "insufficient_data"}
    metrics = con.execute(
        "SELECT count(DISTINCT metric_id) FROM fundamental_metric_values WHERE quality_status='validated' AND period_end=?",
        [latest[0]],
    ).fetchone()[0]
    manual = con.execute(
        "SELECT count(*) FROM fundamental_documents WHERE validation_status='requires_manual_review'"
    ).fetchone()[0]
    total = con.execute("SELECT count(*) FROM fundamental_documents").fetchone()[0]
    result = score(
        key_metrics=metrics,
        total_key_metrics=8,
        age_days=(date.today() - latest[0]).days,
        has_ifrs=False,
        has_ras=True,
        consistency_checks=2,
        consistency_passed=2,
        has_shares=False,
        history_years=max(0, (latest[0] - date(2018, 1, 1)).days / 365.25),
        validated_releases=latest[1],
        manual_ratio=manual / max(total, 1),
        ambiguities=manual,
        stable_methodology=True,
    )
    as_of = (
        con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
        or date.today()
    )
    con.execute(
        "DELETE FROM fundamental_confidence WHERE as_of_date=? AND secid='SBER' AND calculation_version=?",
        [as_of, version],
    )
    con.execute(
        "INSERT INTO fundamental_confidence VALUES (?,'SBER',?,?,?,?,?,current_timestamp)",
        [
            as_of,
            result["data_confidence"],
            result["valuation_confidence"],
            result["backtest_confidence"],
            json.dumps(result["components"]),
            version,
        ],
    )
    return {"status": "success", **result}
