"""Direct official MOEX ISS diagnostics for current portfolio boards."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import requests

from moex_analytics.portfolio_research.portfolio_editor import load_positions

BASE = "https://iss.moex.com/iss"


def validate_current_fallback(row: dict[str, Any], expected: date, board: str) -> bool:
    """Fail closed unless an official current row has identical EOD semantics."""
    return (row.get("BOARDID") == board and str(row.get("TRADEDATE")) == str(expected)
            and row.get("CLOSE") is not None and row.get("TRADINGSESSION") in (None, 3))


def diagnose_portfolio_eod(con: Any, session: requests.Session | None = None,
                           as_of: date | None = None) -> list[dict[str, Any]]:
    client = session or requests.Session()
    as_of = as_of or date.today()
    result = []
    for item in load_positions():
        secid = item["secid"]
        board = con.execute("SELECT board FROM instrument_history_segments WHERE canonical_secid=? "
                            "AND is_primary ORDER BY priority DESC LIMIT 1", [secid]).fetchone()
        board = board[0] if board else "TQBR"
        local = con.execute("SELECT max(trade_date) FROM daily_prices WHERE secid=? AND board=?",
                            [secid, board]).fetchone()[0]
        date_from = (local - timedelta(days=7)) if local else as_of - timedelta(days=7)
        path = (f"history/engines/stock/markets/shares/boards/{board}/securities/{secid}.json")
        url = f"{BASE}/{path}"
        params = {"from": str(date_from), "till": str(as_of), "start": 0,
                  "iss.meta": "off", "iss.only": "history,history.cursor"}
        response = client.get(url, params=params, timeout=(10, 30))
        response.raise_for_status()
        payload = response.json()
        block = payload.get("history", {"columns": [], "data": []})
        rows = [dict(zip(block["columns"], row, strict=True)) for row in block["data"]]
        result.append({"secid": secid, "board": board, "latest_local_eod": local,
            "latest_moex_eod": max((row.get("TRADEDATE") for row in rows), default=None),
            "latest_returned_date": max((row.get("TRADEDATE") for row in rows), default=None),
            "http_status": response.status_code, "rows_returned": len(rows),
            "request_url": response.url, "columns": block["columns"],
            "from": date_from, "till": as_of, "start": 0})
    return result
