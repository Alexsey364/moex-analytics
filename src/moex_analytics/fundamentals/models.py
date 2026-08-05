"""Typed records for SBER fundamentals."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class FundamentalObservation:
    metric_id: str
    period_start: date
    period_end: date
    report_type: str
    accounting_standard: str
    publication_date: date
    available_from: datetime
    value: float
    unit: str
    source: str
    source_document: str
    revision_id: str = "original"
    secid: str = "SBER"


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    method: str
    fair_value: float
    dividend: float
    total_return: float | None
    lower_price: float
    upper_price: float
