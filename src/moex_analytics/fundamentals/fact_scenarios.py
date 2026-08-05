"""Scenarios anchored to validated facts and expanding historical multiples."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import duckdb
import yaml

from .derived import SHARES, current_facts
from .valuation import dividend_discount, justified_pb

VERSION = "sber-fact-valuation-v1"


def calculate(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    facts = current_facts(con)
    if not facts:
        return {"status": "insufficient_data", "reason": "no validated facts"}
    config = yaml.safe_load(path.read_text(encoding="utf-8"))["sber_fact_scenarios"]
    multiples = con.execute(
        "SELECT metric_id,value FROM fundamental_features WHERE metric_id IN ('pe','pb') ORDER BY trade_date"
    ).fetchall()
    pe_values = [v for metric, v in multiples if metric == "pe" and v > 0]
    pb_values = [v for metric, v in multiples if metric == "pb" and v > 0]
    if len(pe_values) < 2 or len(pb_values) < 2:
        return {"status": "insufficient_data", "reason": "too few historical multiples"}
    current = con.execute(
        """SELECT trade_date,close FROM canonical_daily_prices
        WHERE canonical_secid='SBER' ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()
    dividend_row = con.execute(
        """SELECT dividend_per_share FROM dividends WHERE canonical_secid='SBER'
        ORDER BY registry_close_date DESC LIMIT 1"""
    ).fetchone()
    dividend = float(dividend_row[0]) if dividend_row else 0.0
    results = []
    con.execute(
        "DELETE FROM valuation_results WHERE as_of_date=? AND secid='SBER' AND scenario_version=?",
        [current[0], VERSION],
    )
    quartiles = statistics.quantiles(pe_values, n=4)
    for name, rules in config["scenarios"].items():
        profit = facts["net_profit"] * float(rules["profit_multiplier"])
        earnings = profit / SHARES
        quartile = int(rules["pe_quartile"])
        target_pe = quartiles[quartile - 1] if quartile in (1, 3) else statistics.median(pe_values)
        expected_roe = facts["roe"] * float(rules["roe_multiplier"])
        target_pb = justified_pb(expected_roe, float(rules["growth"]), float(rules["cost_of_equity"]))
        expected_dividend = dividend * float(rules["payout_multiplier"])
        ddm = dividend_discount(expected_dividend, float(rules["cost_of_equity"]), float(rules["growth"]))
        methods = {
            "pe": earnings * target_pe,
            "pb_roe": None if target_pb is None else facts["bvps"] * target_pb,
            "dividend_discount": ddm,
        }
        valid = [value for value in methods.values() if value is not None]
        assumptions = {
            "fact_period": str(facts["period_end"]),
            "fact_profit": facts["net_profit"],
            "profit_multiplier": rules["profit_multiplier"],
            "profit": profit,
            "eps": earnings,
            "fact_roe": facts["roe"],
            "roe_multiplier": rules["roe_multiplier"],
            "roe": expected_roe,
            "target_pe": target_pe,
            "cost_of_equity": rules["cost_of_equity"],
            "growth": rules["growth"],
            "dividend_fact": dividend,
            "source": "validated CBR RAS + MOEX market/share/dividend data",
        }
        for method, value in methods.items():
            con.execute(
                """INSERT INTO valuation_results VALUES
                (?,'SBER',?,?,?,?,?,?,?,?,?,current_timestamp)""",
                [
                    current[0],
                    name,
                    method,
                    value,
                    expected_dividend,
                    None if value is None else (value + expected_dividend) / current[1] - 1,
                    min(valid),
                    max(valid),
                    json.dumps(assumptions),
                    VERSION,
                ],
            )
            results.append({"scenario": name, "method": method, "value": value})
    return {
        "status": "success",
        "price_date": str(current[0]),
        "current_price": current[1],
        "fact_period": str(facts["period_end"]),
        "rows": len(results),
        "results": results,
    }
