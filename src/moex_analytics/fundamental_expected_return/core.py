"""Economically interpretable long-horizon decomposition, never a predictive interval."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "fundamental-expected-return-v2-unit-safe"


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def decompose(
    dividend_yield: float | None, growth: float | None, valuation: float | None, horizon: int
) -> dict[str, float | None]:
    scale = horizon / 250
    values = [dividend_yield, growth, valuation]
    if all(value is None or pd.isna(value) for value in values):
        return {"dividend": None, "earnings": None, "rerating": None, "total": None}
    dividend = 0.0 if dividend_yield is None or pd.isna(dividend_yield) else float(dividend_yield) * scale
    earnings = 0.0 if growth is None or pd.isna(growth) else float(np.clip(growth, -0.20, 0.20)) * scale
    # Re-rating is deliberately slow and capped; score is not assumed to be a literal return.
    rerating = (
        0.0 if valuation is None or pd.isna(valuation) else float(np.clip(valuation, -1, 1)) * 0.05 * scale
    )
    return {
        "dividend": dividend,
        "earnings": earnings,
        "rerating": rerating,
        "total": dividend + earnings + rerating,
    }


def run_fundamental_expected_return(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = con.execute("select max(trade_date) from daily_returns").fetchone()[0]
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}".encode()).hexdigest()[:20]
    cached = con.execute(
        "select status,rows from fundamental_return_runs where run_id=?", [run_id]
    ).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "rows": cached[1], "cached": True}
    prices = con.execute(
        "select canonical_secid secid,arg_max(total_return_index,trade_date) price "
        "from daily_returns where canonical_secid<>'IMOEX' group by canonical_secid"
    ).df()
    derived = con.execute(
        "select d.issuer_group,d.growth_score,d.valuation_history_score,d.periods_available "
        "from issuer_derived_fundamental_features d qualify row_number() over(partition by issuer_group "
        "order by trade_date desc)=1"
    ).df()
    mapping = con.execute("select distinct issuer_group,secid from issuer_pit_fundamental_states").df()
    state = prices.merge(mapping, on="secid", how="left").merge(derived, on="issuer_group", how="left")
    dividends = con.execute(
        "select secid,arg_max(dividend_yield_pit,available_from) dividend_yield "
        "from stage30_dividend_pit where available_from<=? group by secid",
        [cutoff],
    ).df()
    state = state.merge(dividends, on="secid", how="left")
    rows = []
    for row in state.itertuples():
        for horizon in (120, 250):
            # Existing growth/valuation fields are dimensionless research scores, not returns.
            parts = decompose(row.dividend_yield, None, None, horizon)
            enough = int(pd.notna(row.dividend_yield))
            status = "INSUFFICIENT_DATA"
            total = parts["total"]
            low = None
            high = None
            rows.append(
                [
                    run_id,
                    cutoff,
                    row.secid,
                    horizon,
                    row.price,
                    parts["dividend"],
                    parts["earnings"],
                    parts["rerating"],
                    total,
                    low,
                    high,
                    min(1, enough / 3),
                    status,
                    "Fundamental valuation range",
                    json.dumps(
                        {
                            "predictive_interval": False,
                            "automatic_promotion": False,
                            "growth_score_used_as_return": False,
                            "limitation": "unit-validated growth and valuation unavailable",
                        }
                    ),
                ]
            )
    frame = pd.DataFrame(
        rows,
        columns=(
            "run_id",
            "as_of_date",
            "secid",
            "horizon",
            "current_price",
            "dividend_component",
            "earnings_component",
            "rerating_component",
            "expected_total_return",
            "fair_value_low",
            "fair_value_high",
            "reliability",
            "status",
            "range_type",
            "details_json",
        ),
    )
    con.execute("BEGIN")
    try:
        con.execute(
            "insert or replace into fundamental_return_runs "
            "values (?,?,current_timestamp,NULL,'running',0,?)",
            [run_id, VERSION, json.dumps({"production_changes": 0})],
        )
        con.register("_f", frame)
        cols = ",".join(frame.columns)
        con.execute(f"insert into fundamental_return_estimates ({cols}) select {cols} from _f")
        con.unregister("_f")
        con.execute(
            "update fundamental_return_runs set finished_at=current_timestamp,status='completed',"
            "rows=? where run_id=?",
            [len(frame), run_id],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"run_id": run_id, "status": "completed", "rows": len(frame), "cached": False}
