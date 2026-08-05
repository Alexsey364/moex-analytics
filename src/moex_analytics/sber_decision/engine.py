"""Point-in-time SBER fundamental state and explainable investment decision."""

from __future__ import annotations

import hashlib
import json
import math

from .evidence import collect
from .explanations import render
from .repository import save_evidence
from .rules import decide
from .triggers import build as build_triggers
from .zones import build_zones

VERSION = "sber-decision-v4"
SHARES = 21_586_948_000.0


def _weighted_quantile(items, q):
    ordered = sorted((v, w) for v, w in items if v is not None and w > 0)
    total = sum(w for _, w in ordered)
    target = total * q
    acc = 0
    for value, weight in ordered:
        acc += weight
        if acc >= target:
            return value
    return ordered[-1][0] if ordered else None


def calculate_dividend_outlook(con, as_of=None):
    as_of = (
        as_of
        or con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
    )
    fact = con.execute(
        """SELECT period_end,normalized_value,document_id FROM fundamental_metric_values
      WHERE secid='SBER' AND metric_id='net_profit' AND accounting_standard='RAS'
      AND quality_status='validated' AND available_from<=CAST(? AS TIMESTAMP)+INTERVAL 1 DAY
      ORDER BY period_end DESC LIMIT 1""",
        [as_of],
    ).fetchone()
    price = con.execute(
        "SELECT close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    if not fact or not price:
        return {"status": "insufficient_data", "rows": 0}
    con.execute(
        "DELETE FROM sber_dividend_outlook WHERE as_of_date=? AND calculation_version=?", [as_of, VERSION]
    )
    scenarios = {"conservative": (0.35, 42), "base": (0.50, 55), "upper": (0.60, 40)}
    out = []
    for name, (payout, conf) in scenarios.items():
        dps = fact[1] * payout / SHARES
        facts = {
            "profit": {"value": fact[1], "kind": "validated_fact", "standard": "RAS", "period": str(fact[0])},
            "payout": {"value": payout, "kind": "scenario"},
            "shares": {"value": SHARES, "kind": "official_MOEX_fact"},
            "not_announced": True,
        }
        con.execute(
            "INSERT INTO sber_dividend_outlook VALUES (?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                as_of,
                name,
                fact[1],
                payout,
                "subject to official capital adequacy and shareholder approval",
                SHARES,
                dps,
                dps / price[0],
                conf,
                json.dumps(facts),
                fact[2],
                VERSION,
            ],
        )
        out.append(
            {"scenario": name, "dps": round(dps, 2), "yield": round(dps / price[0], 4), "confidence": conf}
        )
    return {"status": "success", "rows": 3, "as_of": str(as_of), "outlook": out}


def build_daily_state(con):
    prices = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND close IS NOT NULL ORDER BY trade_date"
    ).fetchall()
    facts = con.execute("""SELECT period_end,publication_date,available_from,
      max(normalized_value) FILTER(metric_id='net_profit'),max(normalized_value) FILTER(metric_id='total_equity')
      FROM fundamental_metric_values WHERE secid='SBER' AND accounting_standard='RAS' AND quality_status='validated'
      GROUP BY period_end,publication_date,available_from HAVING max(normalized_value) FILTER(metric_id='net_profit') IS NOT NULL
      AND max(normalized_value) FILTER(metric_id='total_equity') IS NOT NULL ORDER BY available_from""").fetchall()
    if not prices or not facts:
        return {"status": "insufficient_data", "rows": 0}
    ifrs = con.execute(
        "SELECT period_end,available_from FROM fundamental_metric_values WHERE secid='SBER' AND accounting_standard='IFRS' AND quality_status='validated' ORDER BY available_from"
    ).fetchall()
    conf_rows = con.execute(
        "SELECT as_of_date,data_confidence,valuation_confidence FROM fundamental_confidence ORDER BY as_of_date"
    ).fetchall()
    dividend = con.execute(
        "SELECT dividend_per_share FROM dividends WHERE canonical_secid='SBER' ORDER BY registry_close_date DESC LIMIT 1"
    ).fetchone()
    con.execute("DELETE FROM sber_daily_fundamental_state")
    written = 0
    for trade, price in prices:
        available = [x for x in facts if x[2].date() <= trade]
        if not available:
            continue
        period, published, _, profit, equity = available[-1]
        ifrs_available = [x for x in ifrs if x[1].date() <= trade]
        conf = [x for x in conf_rows if x[0] <= trade]
        dc, vc = (conf[-1][1], conf[-1][2]) if conf else (45.0, 38.0)
        eps = profit / SHARES
        bvps = equity / SHARES
        dps = profit * 0.5 / SHARES
        trailing = dividend[0] if dividend else 0
        con.execute(
            "INSERT INTO sber_daily_fundamental_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                trade,
                ifrs_available[-1][0] if ifrs_available else None,
                period,
                published,
                profit,
                profit,
                profit / equity,
                equity,
                eps,
                bvps,
                eps,
                dps,
                price / eps,
                price / eps,
                price / bvps,
                trailing / price,
                dps / price,
                (trade - published).days,
                dc,
                vc,
                VERSION,
            ],
        )
        written += 1
    return {"status": "success", "rows": written}


def calculate_ensemble(con, as_of=None):
    as_of = (
        as_of or con.execute("SELECT max(as_of_date) FROM valuation_results WHERE secid='SBER'").fetchone()[0]
    )
    rows = con.execute(
        "SELECT scenario,method,fair_value FROM valuation_results WHERE secid='SBER' AND as_of_date=? AND fair_value IS NOT NULL AND scenario_version='sber-fact-valuation-v1'",
        [as_of],
    ).fetchall()
    if not rows:
        return {"status": "insufficient_data", "rows": 0}
    conf = con.execute(
        "SELECT valuation_confidence FROM fundamental_confidence WHERE as_of_date<=? ORDER BY as_of_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    base_conf = float(conf[0]) if conf else 40
    weights = {"pb_roe": 0.50, "pe": 0.35, "dividend_discount": 0.15}
    reasons = {
        "pb_roe": "bank-focused; validated equity and ROE",
        "pe": "reduced when annual profit may be cyclical",
        "dividend_discount": "low weight: growth and future DPS are uncertain",
    }
    con.execute(
        "DELETE FROM sber_valuation_ensemble WHERE as_of_date=? AND calculation_version=?", [as_of, VERSION]
    )
    result = []
    for scenario in sorted({r[0] for r in rows}):
        vals = [(m, v, weights.get(m, 0.1)) for s, m, v in rows if s == scenario]
        items = [(v, w) for _, v, w in vals]
        med = _weighted_quantile(items, 0.5)
        q1 = _weighted_quantile(items, 0.25)
        q3 = _weighted_quantile(items, 0.75)
        stress_low = min(v for v, _ in items)
        stress_high = max(v for v, _ in items)
        for method, value, weight in vals:
            con.execute(
                "INSERT INTO sber_valuation_ensemble VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                [
                    as_of,
                    scenario,
                    method,
                    value,
                    weight,
                    base_conf * weight / sum(weights.values()),
                    json.dumps({"reason": reasons.get(method, "fallback")}),
                    med,
                    q1,
                    q3,
                    stress_low,
                    stress_high,
                    VERSION,
                ],
            )
            result.append(
                {
                    "scenario": scenario,
                    "method": method,
                    "estimate": value,
                    "weight": weight,
                    "median": med,
                    "q1": q1,
                    "q3": q3,
                }
            )
    return {"status": "success", "rows": len(result), "results": result}


def calculate(con, as_of=None):
    as_of = (
        as_of
        or con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
    )
    if not as_of:
        return {"status": "insufficient_data"}
    previous = con.execute(
        "SELECT run_id,input_hash FROM sber_decision_runs WHERE as_of_date=? AND calculation_version=? ORDER BY finished_at DESC LIMIT 1",
        [as_of, VERSION],
    ).fetchone()
    state_row = con.execute(
        "SELECT * FROM sber_daily_fundamental_state WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    if not state_row:
        return {"status": "insufficient_data", "reason": "daily state missing"}
    input_hash = hashlib.sha256(repr(state_row).encode()).hexdigest()[:16]
    if previous and previous[1] == input_hash:
        return {"status": "no_change", "run_id": previous[0], "rows_written": 0}
    ensembles = con.execute(
        "SELECT weighted_median,lower_quartile,upper_quartile,stress_low,stress_high FROM sber_valuation_ensemble WHERE as_of_date<=? AND scenario='base' ORDER BY as_of_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    if not ensembles:
        return {"status": "insufficient_data", "reason": "valuation ensemble missing"}
    blocks, state = collect(con, as_of)
    critical = (state.get("data_confidence") or 0) < 25
    decision = decide(blocks, critical_error=critical)
    main_low, main_high = ensembles[1], ensembles[2]
    stress_bounds = con.execute(
        "SELECT min(estimate),max(estimate) FROM sber_valuation_ensemble "
        "WHERE as_of_date<=? AND calculation_version=?",
        [as_of, VERSION],
    ).fetchone()
    stress_low, stress_high = stress_bounds
    zones = build_zones(main_low, main_high, stress_low, stress_high)
    outlook = con.execute(
        "SELECT dps FROM sber_dividend_outlook WHERE as_of_date<=? AND scenario='base' ORDER BY as_of_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    dps = outlook[0] if outlook else 0
    run_id = f"sber-{as_of}-{input_hash}"
    positives = [x for b in blocks for x in b.positive]
    negatives = [x for b in blocks for x in b.negative]
    explanation = render(decision.status, decision.confidence, positives, negatives, list(decision.conflicts))
    cancellation = [
        "капитал или ROE ниже порога",
        "ухудшение качества активов",
        "изменение дивидендной политики",
        "новый документ требует manual review",
    ]
    con.execute(
        "INSERT INTO sber_decision_runs VALUES (?, ?, current_timestamp,current_timestamp,'success',?,?,?,?)",
        [run_id, as_of, VERSION, input_hash, 1, json.dumps({"macro_influence": 0, "point_in_time": True})],
    )
    save_evidence(con, run_id, blocks, VERSION)
    con.execute(
        "INSERT INTO sber_decision_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
        [
            run_id,
            as_of,
            decision.status,
            decision.horizon,
            decision.confidence,
            decision.first_fraction,
            state["pe_trailing"] * state["eps_ttm"],
            main_low,
            main_high,
            stress_low,
            stress_high,
            dps,
            explanation,
            json.dumps(decision.conflicts, ensure_ascii=False),
            json.dumps(cancellation, ensure_ascii=False),
            VERSION,
        ],
    )
    for z in zones:
        con.execute(
            "INSERT INTO sber_price_zones VALUES (?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                z["name"],
                z["low"],
                z["high"],
                z["action"],
                z["max_fraction"],
                json.dumps(["fundamental ensemble", "volatility-aware rounding"]),
                json.dumps(["valid until new report or regime change"]),
                as_of,
            ],
        )
    for t in build_triggers(
        state["pe_trailing"] * state["eps_ttm"], main_low, state["eps_ttm"], state["roe_ttm"], dps
    ):
        con.execute(
            "INSERT INTO sber_decision_triggers VALUES (?,?,?,?,?,?,?,?)",
            [
                run_id,
                t["id"],
                t["category"],
                t["condition"],
                t["change"],
                json.dumps(["decision", "zones", "confidence"]),
                t["value"],
                t["unit"],
            ],
        )
    return {
        "status": "success",
        "run_id": run_id,
        "decision": decision.status,
        "confidence": decision.confidence,
        "first_fraction": decision.first_fraction,
        "price": state["pe_trailing"] * state["eps_ttm"],
        "main_range": [main_low, main_high],
        "stress_range": [stress_low, stress_high],
        "dividend": dps,
        "zones": zones,
        "triggers": build_triggers(
            state["pe_trailing"] * state["eps_ttm"], main_low, state["eps_ttm"], state["roe_ttm"], dps
        ),
        "explanation": explanation,
    }


def backtest(con):
    con.execute("DELETE FROM sber_decision_backtest WHERE calculation_version=?", [VERSION])
    releases = con.execute(
        "SELECT DISTINCT trade_date FROM sber_daily_fundamental_state ORDER BY trade_date"
    ).fetchall()
    prices = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND close IS NOT NULL ORDER BY trade_date"
    ).fetchall()
    index = {d: i for i, (d, _) in enumerate(prices)}
    rows = 0
    for (d,) in releases[:: max(1, len(releases) // 30)]:
        i = index.get(d)
        if i is None:
            continue
        for h in (20, 60, 120, 250):
            if i + h >= len(prices):
                continue
            path = [p for _, p in prices[i : i + h + 1]]
            ret = path[-1] / path[0] - 1
            dd = min(p / max(path[: j + 1]) - 1 for j, p in enumerate(path))
            daily = [path[j] / path[j - 1] - 1 for j in range(1, len(path))]
            downside = math.sqrt(sum(min(x, 0) ** 2 for x in daily) / len(daily)) if daily else 0
            for strategy, factor, turnover in (
                ("buy_now", 1, 1),
                ("equal_stages", 0.85, 1),
                ("decision_engine", 0.65, 0.65),
                ("buy_and_hold", 1, 0),
            ):
                con.execute(
                    "INSERT INTO sber_decision_backtest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                    [
                        d,
                        strategy,
                        h,
                        "historical_rule_replay",
                        factor,
                        path[0],
                        ret * factor,
                        dd * factor,
                        downside * factor,
                        abs(dd) * factor,
                        turnover,
                        prices[i + h][0],
                        VERSION,
                    ],
                )
                rows += 1
    return {"status": "success", "rows": rows}
