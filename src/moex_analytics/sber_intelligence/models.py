"""Typed SBER intelligence records."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EventCandidate:
    event_id: str
    event_type: str
    subtype: str
    title: str
    available_from: datetime
    source_id: str
    source_url: str
    document_id: str
    validation_status: str = "manual_review"
    actual: float | None = None
    unit: str | None = None
