"""Deterministic event identity and source-copy grouping."""

import hashlib
from datetime import datetime


def canonical_key(entity: str, event_type: str, occurred_at: datetime, metric: str = "") -> str:
    day = occurred_at.date().isoformat()
    return hashlib.sha256(f"{entity}|{event_type}|{day}|{metric}".encode()).hexdigest()[:24]


def choose_primary(copies: list[dict], trust: dict[str, float]) -> dict:
    return sorted(copies, key=lambda x: (-trust.get(x["source_id"], 0), x["available_from"]))[0]


def first_confirmed_time(copies: list[dict]) -> datetime:
    official = [x["available_from"] for x in copies if x.get("official_status") == "official"]
    return min(official or [x["available_from"] for x in copies])
