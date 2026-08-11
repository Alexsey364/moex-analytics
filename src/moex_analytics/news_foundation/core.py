"""Stage 66 source governance and immutable provenance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from moex_analytics.config import PROJECT_ROOT

DDL = """
CREATE TABLE IF NOT EXISTS news_source_registry(
 source_id VARCHAR PRIMARY KEY,name VARCHAR,domain VARCHAR,source_type VARCHAR,tier INTEGER,
 official BOOLEAN,access_method VARCHAR,endpoint VARCHAR,license VARCHAR,robots_status VARCHAR,
 rate_limit_per_minute INTEGER,timestamp_semantics VARCHAR,timezone VARCHAR,archive_depth VARCHAR,
 reproducibility VARCHAR,status VARCHAR,registered_at TIMESTAMP,details_json JSON);
CREATE TABLE IF NOT EXISTS news_provenance(
 news_id VARCHAR,source_id VARCHAR,url VARCHAR,retrieved_at TIMESTAMPTZ,published_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ,content_hash VARCHAR,retention_policy VARCHAR,
 PRIMARY KEY(news_id,source_id,url,content_hash));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _validate(sources: list[dict[str, Any]]) -> None:
    ids = [row["source_id"] for row in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate news source_id")
    endpoints = [row.get("endpoint") for row in sources if row.get("endpoint")]
    if len(endpoints) != len(set(endpoints)):
        raise ValueError("duplicate news source endpoint")
    for row in sources:
        if row["status"].startswith("active") and (
            "review_required" in row["license"] or "review_required" in row["robots_status"]
        ):
            raise ValueError(f"ungoverned source cannot be active: {row['source_id']}")


def load_source_registry(con: Any, path: Path | None = None) -> dict[str, Any]:
    ensure_schema(con)
    target = path or PROJECT_ROOT / "config" / "news_sources.yaml"
    sources = yaml.safe_load(target.read_text(encoding="utf-8"))["sources"]
    _validate(sources)
    columns = ("source_id", "name", "domain", "source_type", "tier", "official",
        "access_method", "endpoint", "license", "robots_status", "rate_limit_per_minute",
        "timestamp_semantics", "timezone", "archive_depth", "reproducibility", "status")
    for row in sources:
        values = [row.get(column) for column in columns]
        con.execute("INSERT OR REPLACE INTO news_source_registry (" + ",".join(columns) +
            ",registered_at,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "current_timestamp,?)", [*values, json.dumps({"full_text_retention": False})])
    active = sum(row["status"].startswith("active") for row in sources)
    return {"sources": len(sources), "active": active, "disabled": len(sources) - active,
            "duplicates": 0, "full_text_retention": False}


def source_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    total, active, official = con.execute(
        "SELECT count(*),count(*) FILTER(WHERE status LIKE 'active%'),"
        "count(*) FILTER(WHERE official) FROM news_source_registry"
    ).fetchone()
    return {"sources": total, "active": active, "official": official}
