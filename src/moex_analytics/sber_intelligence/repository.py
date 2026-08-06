"""Point-in-time DuckDB persistence."""

import json

from .event_study import summarize
from .reaction import reaction

VERSION = "sber-intelligence-v5"


def calculate_reactions(con) -> dict:
    prices = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND close IS NOT NULL ORDER BY trade_date"
    ).fetchall()
    market = dict(
        con.execute(
            "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='IMOEX' AND close IS NOT NULL ORDER BY trade_date"
        ).fetchall()
    )
    events = con.execute(
        "SELECT event_id,available_from,event_type FROM sber_events WHERE validation_status='validated' AND available_from IS NOT NULL ORDER BY available_from"
    ).fetchall()
    con.execute("DELETE FROM sber_event_reactions WHERE calculation_version=?", [VERSION])
    written = 0
    for event_id, available, _event_type in events:
        same = con.execute(
            "SELECT count(*) FROM sber_events WHERE CAST(available_from AS DATE)=CAST(? AS DATE) AND validation_status='validated'",
            [available],
        ).fetchone()[0]
        for row in reaction(prices, market, available, same):
            con.execute(
                """INSERT INTO sber_event_reactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)""",
                [
                    event_id,
                    row["window"],
                    row["anchor"],
                    row["exit"],
                    row["raw"],
                    row["imoex"],
                    None,
                    row["abnormal"],
                    None,
                    row["volume_change"],
                    row["volatility_change"],
                    row["max_gain"],
                    row["max_drawdown"],
                    row["sessions_to_max"],
                    row["session"],
                    row["confounding"],
                    json.dumps(row["confounders"]),
                    VERSION,
                ],
            )
            written += 1
    return {"events": len(events), "rows": written}


def build_studies(con) -> dict:
    con.execute("DELETE FROM sber_event_studies WHERE calculation_version=?", [VERSION])
    written = 0
    groups = con.execute(
        "SELECT e.event_type,r.event_window,list(r.abnormal_imoex),list(r.max_drawdown) FROM sber_event_reactions r JOIN sber_events e USING(event_id) WHERE r.abnormal_imoex IS NOT NULL AND r.calculation_version=? GROUP BY 1,2",
        [VERSION],
    ).fetchall()
    for event_type, window, values, drawdowns in groups:
        s = summarize(values)
        con.execute(
            "INSERT INTO sber_event_studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                event_type,
                window,
                s["sample_size"],
                s["mean"],
                s["median"],
                s["positive_frequency"],
                s["q25"],
                s["q75"],
                s["best"],
                s["worst"],
                sum(drawdowns) / len(drawdowns),
                s["quality"],
                "all",
                VERSION,
            ],
        )
        written += 1
    return {"rows": written}


def build_live_state(con) -> dict:
    stats = con.execute(
        """SELECT max(available_from),max(available_from) FILTER(validation_status='validated'),count(*) FILTER(validation_status<>'validated'),count(*) FILTER(validation_status='manual_review'),count(DISTINCT source_id) FROM sber_events"""
    ).fetchone()
    market = con.execute(
        "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
    ).fetchone()[0]
    fundamental = con.execute(
        "SELECT max(available_from) FROM fundamental_documents WHERE validation_status='validated'"
    ).fetchone()[0]
    upcoming = con.execute(
        "SELECT min(scheduled_at) FROM sber_events WHERE scheduled_at>current_timestamp"
    ).fetchone()[0]
    total = con.execute("SELECT count(*) FROM sber_events").fetchone()[0]
    official = stats[4]
    confidence = max(0, min(100, 35 + min(30, official * 5) + min(25, total) + (-stats[3] * 3)))
    freshness = "stale" if not stats[0] else "available"
    con.execute("DELETE FROM sber_live_information_state WHERE version=?", [VERSION])
    con.execute(
        "INSERT INTO sber_live_information_state VALUES (current_timestamp,?,?,?,?,?,?,?,?,?,?,?)",
        [
            market,
            fundamental,
            stats[0],
            stats[1],
            upcoming,
            "elevated" if stats[2] else "normal",
            freshness,
            stats[2],
            stats[3],
            confidence,
            VERSION,
        ],
    )
    return {
        "information_confidence": confidence,
        "unresolved": stats[2],
        "manual_review": stats[3],
        "upcoming": str(upcoming) if upcoming else None,
    }


def status(con) -> dict:
    result = {
        name: con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        for name in (
            "sber_information_sources",
            "sber_information_documents",
            "sber_events",
            "sber_expectations",
            "sber_event_quality_issues",
        )
    }
    for name in ("sber_surprises", "sber_event_reactions", "sber_event_studies"):
        result[name] = con.execute(
            f"SELECT count(*) FROM {name} WHERE calculation_version=?", [VERSION]
        ).fetchone()[0]
    return result
