"""Typed records exchanged by macro sources and persistence code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class SeriesDefinition:
    series_id: str
    name: str
    unit: str
    frequency: str
    source: str
    endpoint: str
    start_date: date | None
    publication_rule: str
    revision_rule: str
    is_point_in_time_safe: bool
    notes: str = ""


@dataclass(frozen=True)
class Observation:
    series_id: str
    observation_date: date
    release_date: date
    available_from: datetime
    value: float
    vintage: str
    source: str
