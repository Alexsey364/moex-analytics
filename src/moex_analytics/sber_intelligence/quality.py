"""Confounding labels and persisted quality checks."""

import hashlib


def confounding_status(same_day_events: int, market_abs_return: float, dividend_day: bool = False):
    factors = []
    if same_day_events > 1:
        factors.append("another event same day")
    if market_abs_return > 0.03:
        factors.append("broad market shock")
    if dividend_day:
        factors.append("dividend record/ex-date proximity")
    if len(factors) >= 2:
        return "heavily_confounded", factors
    if factors:
        return "partially_confounded", factors
    return "clean_event", factors


def run(con) -> dict:
    con.execute("DELETE FROM sber_event_quality_issues")
    rows = con.execute(
        "SELECT event_id,available_from,source_url,validation_status FROM sber_events"
    ).fetchall()
    issues = 0
    for event_id, available, url, status in rows:
        for kind, description in (
            ("missing_available_from", "missing market availability") if available is None else (None, None),
            ("missing_source", "missing primary source link") if not url else (None, None),
        ):
            if kind:
                issue = hashlib.sha256(f"{event_id}|{kind}".encode()).hexdigest()[:24]
                con.execute(
                    "INSERT INTO sber_event_quality_issues VALUES (?,?,?,?,?,current_timestamp)",
                    [issue, event_id, kind, "critical", description],
                )
                issues += 1
        if status == "manual_review":
            issue = hashlib.sha256(f"{event_id}|manual".encode()).hexdigest()[:24]
            con.execute(
                "INSERT INTO sber_event_quality_issues VALUES (?,?,?,?,?,current_timestamp)",
                [issue, event_id, "manual_review", "warning", "event requires manual confirmation"],
            )
            issues += 1
    return {"issues": issues}
