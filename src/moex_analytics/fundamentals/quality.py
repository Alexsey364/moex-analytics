"""Transparent quality checks; suspicious data are never silently repaired."""

from .models import FundamentalObservation


def inspect(rows: list[FundamentalObservation]) -> list[dict]:
    issues = []
    seen = set()
    for r in rows:
        key = (r.metric_id, r.period_end, r.accounting_standard, r.revision_id)
        if key in seen:
            issues.append({"metric_id": r.metric_id, "issue_type": "duplicate", "severity": "error"})
        seen.add(key)
        if r.publication_date < r.period_end:
            issues.append(
                {"metric_id": r.metric_id, "issue_type": "publication_before_period_end", "severity": "error"}
            )
        if r.available_from.date() < r.publication_date:
            issues.append(
                {"metric_id": r.metric_id, "issue_type": "available_before_publication", "severity": "error"}
            )
        if r.accounting_standard not in {"IFRS", "RAS"}:
            issues.append({"metric_id": r.metric_id, "issue_type": "unknown_standard", "severity": "error"})
        if r.metric_id in {"assets", "equity", "ordinary_shares", "client_funds"} and r.value < 0:
            issues.append(
                {"metric_id": r.metric_id, "issue_type": "impossible_negative", "severity": "error"}
            )
    return issues
