"""Stage 68: descriptive, non-causal EOD market reaction memory."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

HORIZONS = (1, 5, 20, 60)
VERSION = "news-reaction-v1"
DDL = """
CREATE TABLE IF NOT EXISTS news_reaction_runs(
 run_id VARCHAR PRIMARY KEY,cutoff_date DATE,status VARCHAR,items INTEGER,rows_written INTEGER,
 intraday_status VARCHAR,created_at TIMESTAMP,details VARCHAR);
CREATE TABLE IF NOT EXISTS news_reaction_memory(
 news_id VARCHAR,story_id VARCHAR,secid VARCHAR,event_type VARCHAR,available_from TIMESTAMPTZ,
 anchor_date DATE,horizon INTEGER,anchor_close DOUBLE,future_close DOUBLE,market_return DOUBLE,
 relative_to_imoex DOUBLE,volume_ratio DOUBLE,persistence VARCHAR,interpretation VARCHAR,
 intraday_status VARCHAR,point_in_time_safe BOOLEAN,calculation_version VARCHAR,
 PRIMARY KEY(news_id,secid,horizon,calculation_version));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _trajectory(values: list[float], final_return: float) -> str:
    if len(values) < 2:
        return "unavailable"
    early = values[min(1, len(values) - 1)] / values[0] - 1
    if early * final_return < 0:
        return "reversed"
    if abs(final_return) < abs(early) / 2:
        return "faded"
    return "continued"


def build_reaction_memory(con: Any, cutoff: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = cutoff or con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    if cutoff is None:
        return {"status": "insufficient_price_history", "rows": 0}
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}".encode()).hexdigest()[:20]
    con.execute("DELETE FROM news_reaction_memory WHERE calculation_version=?", [VERSION])
    items = con.execute("SELECT news_id,story_id,event_type,available_from,entities_json,tone "
        "FROM news_items WHERE CAST(available_from AS DATE)<=? ORDER BY available_from", [cutoff]).fetchall()
    written = 0
    for news_id, story_id, event_type, available_from, entities, tone in items:
        entities_text = str(entities)
        secids = [row[0] for row in con.execute("SELECT DISTINCT canonical_secid "
            "FROM canonical_daily_prices WHERE canonical_secid<>'IMOEX'").fetchall()
            if row[0] in entities_text]
        secids = secids or ["IMOEX"]
        for secid in secids:
            prices = con.execute("SELECT trade_date,close,volume FROM canonical_daily_prices "
                "WHERE canonical_secid=? AND trade_date>=CAST(? AS DATE) AND trade_date<=? "
                "ORDER BY trade_date LIMIT 61", [secid, available_from, cutoff]).fetchall()
            if not prices:
                continue
            for horizon in HORIZONS:
                if len(prices) <= horizon:
                    continue
                anchor, future = prices[0], prices[horizon]
                market_return = future[1] / anchor[1] - 1
                imoex = con.execute("SELECT close FROM canonical_daily_prices WHERE canonical_secid="
                    "'IMOEX' AND trade_date IN (?,?) ORDER BY trade_date", [anchor[0], future[0]]).fetchall()
                relative = market_return - (imoex[-1][0] / imoex[0][0] - 1) if len(imoex) == 2 else None
                volumes = [float(row[2]) for row in prices[: horizon + 1] if row[2] is not None]
                volume_ratio = volumes[-1] / volumes[0] if len(volumes) > 1 and volumes[0] else None
                path = [float(row[1]) for row in prices[: horizon + 1]]
                persistence = _trajectory(path, market_return)
                expected = 1 if tone == "positive_wording" else -1 if tone == "negative_wording" else 0
                actual = 1 if market_return > 0 else -1
                interpretation = "tone_aligned" if expected and expected == actual else (
                    "tone_not_confirmed" if expected else "descriptive_only")
                con.execute("INSERT INTO news_reaction_memory (news_id,story_id,secid,event_type,"
                    "available_from,anchor_date,horizon,anchor_close,future_close,market_return,"
                    "relative_to_imoex,volume_ratio,persistence,interpretation,intraday_status,"
                    "point_in_time_safe,calculation_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "'unavailable_no_intraday_source',true,?)", [news_id, story_id, secid, event_type,
                    available_from, anchor[0], horizon, anchor[1], future[1], market_return, relative,
                    volume_ratio, persistence, interpretation, VERSION])
                written += 1
    con.execute("INSERT OR REPLACE INTO news_reaction_runs (run_id,cutoff_date,status,items,rows_written,"
        "intraday_status,created_at,details) VALUES (?,?,'completed',?,?,'unavailable_no_intraday_source',"
        "current_timestamp,'descriptive association; no causal claim')",
        [run_id, cutoff, len(items), written])
    return {"run_id": run_id, "status": "completed", "items": len(items), "rows": written,
            "intraday": "unavailable_no_intraday_source"}


def reaction_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT count(*),count(DISTINCT news_id),min(anchor_date),max(anchor_date) "
                      "FROM news_reaction_memory").fetchone()
    return dict(zip(("rows", "items", "date_from", "date_to"), row, strict=True))
