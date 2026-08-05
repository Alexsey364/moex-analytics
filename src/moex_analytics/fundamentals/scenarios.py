"""Configuration-driven scenario calculation."""

import json
from datetime import datetime
from pathlib import Path

import duckdb
import yaml

from .valuation import dividend_discount, pb_roe_scenario, pe_scenario


def load_scenarios(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["sber_valuation"]


def calculate_scenario(name: str, assumptions: dict, current_price: float | None = None) -> list[dict]:
    assumptions = {key: float(value) for key, value in assumptions.items()}
    shares = assumptions["shares"]
    profit = assumptions["profit"]
    payout = assumptions["payout_ratio"]
    eps = profit / shares
    dividend = eps * payout
    values = {
        "pe": pe_scenario(profit, shares, assumptions["target_pe"]),
        "pb_roe": pb_roe_scenario(
            assumptions["bvps"],
            assumptions["roe"],
            assumptions["growth"],
            assumptions["cost_of_equity"],
            assumptions.get("target_pb"),
        ),
        "dividend_discount": dividend_discount(
            dividend, assumptions["cost_of_equity"], assumptions["growth"]
        ),
    }
    valid = [v for v in values.values() if v is not None]
    return [
        {
            "scenario": name,
            "method": method,
            "fair_value": value,
            "dividend": dividend,
            "total_return": None
            if current_price in (None, 0) or value is None
            else (value + dividend) / current_price - 1,
            "lower_price": min(valid),
            "upper_price": max(valid),
        }
        for method, value in values.items()
    ]


def sensitivity(profits: list[float], multiples: list[float], shares: float) -> list[dict]:
    return [{"profit": p, "pe": m, "price": pe_scenario(p, shares, m)} for p in profits for m in multiples]


def calculate_all(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    config = load_scenarios(path)
    version = config["version"]
    price_row = con.execute(
        """SELECT trade_date,close FROM canonical_daily_prices
        WHERE canonical_secid='SBER' ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()
    if not price_row:
        return {"status": "insufficient_data", "reason": "SBER market price is absent", "rows": 0}
    as_of, current_price = price_row
    rows = []
    con.execute(
        "DELETE FROM valuation_scenarios WHERE as_of_date=? AND secid='SBER' AND scenario_version=?",
        [as_of, version],
    )
    con.execute(
        "DELETE FROM valuation_results WHERE as_of_date=? AND secid='SBER' AND scenario_version=?",
        [as_of, version],
    )
    for name, assumptions in config["scenarios"].items():
        con.execute(
            "INSERT INTO valuation_scenarios VALUES (?,'SBER',?,?,?,?)",
            [as_of, name, json.dumps(assumptions), version, datetime.now()],
        )
        for row in calculate_scenario(name, assumptions, current_price):
            con.execute(
                "INSERT INTO valuation_results VALUES (?,'SBER',?,?,?,?,?,?,?,?,?,?)",
                [
                    as_of,
                    name,
                    row["method"],
                    row["fair_value"],
                    row["dividend"],
                    row["total_return"],
                    row["lower_price"],
                    row["upper_price"],
                    json.dumps({"current_price": current_price}),
                    version,
                    datetime.now(),
                ],
            )
            rows.append(row)
    return {
        "status": "success",
        "as_of_date": str(as_of),
        "current_price": current_price,
        "rows": len(rows),
        "results": rows,
    }
