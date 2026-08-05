"""Typed values returned by the explainable SBER decision engine."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Evidence:
    block_id: str
    score: float
    confidence: float
    status: str
    positive: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    data_date: date | None = None


@dataclass(frozen=True)
class Decision:
    status: str
    horizon: int
    confidence: float
    first_fraction: float
    conflicts: tuple[str, ...] = ()
