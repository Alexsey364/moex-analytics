"""Versioned contracts for instrument-specific adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterContract:
    version: str
    sources: tuple[str, ...]
    required_fields: tuple[str, ...]
    validation_rules: tuple[str, ...]
    point_in_time_contract: str
    confidence_method: str
    failure_status: str = "unavailable"


class BaseAdapter(ABC):
    contract: AdapterContract

    @abstractmethod
    def validate(self, payload: dict) -> tuple[bool, list[str]]: ...


class InstrumentDataAdapter(BaseAdapter):
    pass


class FundamentalAdapter(BaseAdapter):
    pass


class DividendPolicyAdapter(BaseAdapter):
    pass


class ValuationAdapter(BaseAdapter):
    pass


class EventAdapter(BaseAdapter):
    pass


class DerivativeAdapter(BaseAdapter):
    pass


class DecisionEvidenceProvider(BaseAdapter):
    pass


class ResearchPipeline(ABC):
    version: str

    @abstractmethod
    def run(self, con, secid: str) -> dict: ...
