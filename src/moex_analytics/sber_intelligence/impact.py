"""Experimental event impact and auditable decision change helpers."""

import json


def score(
    *, direction: int, severity: float, source_confidence: float, clean: bool, freshness: float
) -> dict:
    value = direction * severity * source_confidence * (1 if clean else 0.5) * freshness
    status = (
        "experimental_positive"
        if value > 0.2
        else "experimental_negative"
        if value < -0.2
        else "high_uncertainty"
    )
    return {"score": max(-1, min(1, value)), "status": status}


def proposed_adjustment(
    parameter: str, old_value: float | None, proposed_value: float | None, basis: str, numeric_validated: bool
) -> dict:
    return {
        "parameter": parameter,
        "old_value": old_value,
        "proposed_value": proposed_value,
        "basis": basis,
        "requires_manual_confirmation": not numeric_validated,
        "status": "applicable" if numeric_validated else "proposed_only",
    }


def build_impacts(con, version="sber-intelligence-v1") -> dict:
    con.execute("DELETE FROM sber_event_impacts")
    rows = 0
    events = con.execute(
        "SELECT event_id,event_type,related_entity,severity,validation_status FROM sber_events"
    ).fetchall()
    for event_id, event_type, entity, _severity, status in events:
        numeric = (
            con.execute("SELECT count(*) FROM sber_event_metrics WHERE event_id=?", [event_id]).fetchone()[0]
            > 0
        )
        allowed = (
            status == "validated"
            and entity == "SBER"
            and numeric
            and event_type in {"financial", "dividend", "capital", "shares"}
        )
        impact_status = "confirmed_material" if allowed else "informational_only"
        con.execute(
            "INSERT INTO sber_event_impacts VALUES (?,?,?,?,?,?,?,current_timestamp)",
            [
                event_id,
                0.0,
                impact_status,
                json.dumps([]),
                allowed,
                json.dumps(["weight=0 until historical value is proven"]),
                version,
            ],
        )
        rows += 1
    return {
        "rows": rows,
        "auto_apply_allowed": con.execute(
            "SELECT count(*) FROM sber_event_impacts WHERE auto_apply_allowed"
        ).fetchone()[0],
    }


def write_change_log(
    con,
    previous_id,
    new_id,
    event_id,
    old_status,
    new_status,
    changed_parameters,
    version="sber-intelligence-v1",
):
    if old_status == new_status and not changed_parameters:
        return {"written": 0, "reason": "no decision change"}
    con.execute(
        "INSERT INTO sber_decision_change_log VALUES (current_timestamp,?,?,?,?,?,?,?,?,?,?,?)",
        [
            previous_id,
            new_id,
            event_id,
            json.dumps(["event_information"]),
            json.dumps(changed_parameters),
            old_status,
            new_status,
            json.dumps({}),
            json.dumps({}),
            "deterministic validated event change",
            version,
        ],
    )
    return {"written": 1}
