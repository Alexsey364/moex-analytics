"""Expectation coverage and point-in-time surprise persistence."""

from .repository import VERSION
from .surprises import calculate


def consensus(values: list[float]) -> dict:
    if not values:
        return {"value": None, "sample_size": 0, "coverage": "unavailable"}
    ordered = sorted(values)
    middle = len(ordered) // 2
    value = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "value": value,
        "sample_size": len(ordered),
        "coverage": "limited" if len(ordered) < 10 else "available",
    }


def calculate_all(con) -> dict:
    con.execute("DELETE FROM sber_surprises WHERE calculation_version=?", [VERSION])
    events = con.execute(
        """SELECT m.event_id,m.metric_id,m.value,m.available_from
        FROM sber_event_metrics m JOIN sber_events e USING(event_id)
        WHERE e.validation_status='validated'"""
    ).fetchall()
    written = 0
    for event_id, metric, actual, available in events:
        forecasts = con.execute(
            """SELECT estimate,analyst_count,confidence FROM sber_expectations
            WHERE metric_id=? AND available_from<=? AND validation_status='validated'
            ORDER BY available_from DESC LIMIT 1""",
            [metric, available],
        ).fetchone()
        result = calculate(actual, forecasts[0] if forecasts else None, forecasts[1] or 0 if forecasts else 0)
        con.execute(
            "INSERT INTO sber_surprises VALUES (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                event_id,
                metric,
                result["actual"],
                result["consensus"],
                result["difference"],
                result["percentage"],
                result["standardized"],
                result["direction"],
                forecasts[1] if forecasts else 0,
                result["confidence"],
                VERSION,
            ],
        )
        written += 1
    coverage = con.execute(
        "SELECT count(*) FROM sber_expectations WHERE validation_status='validated'"
    ).fetchone()[0]
    return {
        "rows": written,
        "validated_expectations": coverage,
        "coverage": "unavailable" if coverage == 0 else "limited",
    }
