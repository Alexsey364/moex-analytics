"""Validation rules for official point-in-time events."""

AUTO_NUMERIC_TYPES = {"financial", "dividend", "capital", "shares", "regulatory"}


def validate(event: dict) -> tuple[str, list[str]]:
    issues = []
    if not event.get("available_from"):
        issues.append("missing available_from")
    if not event.get("source_url"):
        issues.append("missing source URL")
    if not event.get("point_in_time_safe", False):
        issues.append("not point-in-time safe")
    status = "validated" if not issues and event.get("official_status") == "official" else "manual_review"
    return status, issues


def may_auto_apply(event_type: str, validated: bool, numeric_fact: bool) -> bool:
    return validated and numeric_fact and event_type in AUTO_NUMERIC_TYPES
