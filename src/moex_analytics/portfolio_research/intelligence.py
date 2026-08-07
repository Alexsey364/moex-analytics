"""Real portfolio intelligence with conservative evidence gates."""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np

from .core import (
    annualized_volatility,
    downside_volatility,
    hierarchical_risk_parity,
    inverse_volatility_weights,
    max_drawdown,
    maximum_diversification_weights,
    minimum_variance_weights,
    normalize_weights,
    risk_contributions,
    transaction_cost,
)
from .external_methods import covariance_shrinkage
from .portfolio_v14 import _panel, parse_local_portfolio

VERSION = "portfolio-intelligence-v1"
DDL = """
CREATE TABLE IF NOT EXISTS portfolio_reconciliation(snapshot_id VARCHAR PRIMARY KEY,equity_value DOUBLE,cash_position DOUBLE,account_reference_value DOUBLE,broker_reference_profit DOUBLE,reconstructed_profit DOUBLE,difference DOUBLE,status VARCHAR,equity_only BOOLEAN,created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS issuer_official_sources(secid VARCHAR,source_id VARCHAR,source_url VARCHAR,source_type VARCHAR,reporting_standard VARCHAR,status VARCHAR,last_checked TIMESTAMP,notes VARCHAR,PRIMARY KEY(secid,source_id));
CREATE TABLE IF NOT EXISTS issuer_fundamental_values(secid VARCHAR,metric VARCHAR,reporting_standard VARCHAR,period_start DATE,period_end DATE,publication_date DATE,available_from TIMESTAMP,source VARCHAR,document VARCHAR,page_table VARCHAR,raw_value DOUBLE,normalized_value DOUBLE,unit VARCHAR,validation_status VARCHAR,revision VARCHAR,PRIMARY KEY(secid,metric,period_end,reporting_standard,revision));
CREATE TABLE IF NOT EXISTS issuer_valuation_states(snapshot_id VARCHAR,secid VARCHAR,valuation_family VARCHAR,main_low DOUBLE,main_high DOUBLE,conservative_low DOUBLE,conservative_high DOUBLE,stress_low DOUBLE,stress_high DOUBLE,confidence VARCHAR,zone VARCHAR,data_age_days INTEGER,limitations VARCHAR,status VARCHAR,PRIMARY KEY(snapshot_id,secid));
CREATE TABLE IF NOT EXISTS instrument_regime_risk(snapshot_id VARCHAR,secid VARCHAR,regime VARCHAR,volatility_state VARCHAR,drawdown_state VARCHAR,factor VARCHAR,factor_status VARCHAR,factor_sign DOUBLE,sign_switch_warning BOOLEAN,confidence VARCHAR,accumulation_effect VARCHAR,evidence_json JSON,PRIMARY KEY(snapshot_id,secid));
CREATE TABLE IF NOT EXISTS portfolio_action_map(snapshot_id VARCHAR,secid VARCHAR,current_price DOUBLE,quantity DOUBLE,current_value DOUBLE,equity_weight DOUBLE,risk_contribution DOUBLE,average_price DOUBLE,profit_loss DOUBLE,valuation_status VARCHAR,dividend_status VARCHAR,regime_status VARCHAR,fundamental_confidence VARCHAR,portfolio_fit VARCHAR,target_status VARCHAR,allowed_action VARCHAR,next_tranche_pct DOUBLE,evidence_for_json JSON,evidence_against_json JSON,invalidation_triggers_json JSON,PRIMARY KEY(snapshot_id,secid));
CREATE TABLE IF NOT EXISTS portfolio_alternatives_v15(snapshot_id VARCHAR,period_type VARCHAR,date_from DATE,date_to DATE,method VARCHAR,weights_json JSON,cagr DOUBLE,volatility DOUBLE,downside DOUBLE,max_drawdown DOUBLE,risk_contribution_json JSON,turnover DOUBLE,cost DOUBLE,warning VARCHAR,PRIMARY KEY(snapshot_id,period_type,method));
CREATE TABLE IF NOT EXISTS portfolio_intelligence_shadow(intelligence_id VARCHAR PRIMARY KEY,created_at TIMESTAMP,as_of_date DATE,portfolio_snapshot_id VARCHAR,positions_json JSON,fundamentals_json JSON,valuations_json JSON,dividends_json JSON,regimes_json JSON,action_map_json JSON,risk_json JSON,model_versions_json JSON,input_hash VARCHAR,immutable BOOLEAN);
"""
SOURCES = {
    "X5": [("ir", "https://www.x5.ru/en/investors/financial-and-operational-results/", "issuer_ir", "IFRS")],
    "SBERP": [
        ("ir", "https://www.sberbank.com/investor-relations/", "issuer_ir", "IFRS"),
        ("cbr", "https://www.cbr.ru/banking_sector/credit/", "regulator", "RAS"),
    ],
    "LKOH": [("ir", "https://www.lukoil.com/InvestorAndShareholderCenter", "issuer_ir", "IFRS")],
    "LSNGP": [("ir", "https://rosseti-lenenergo.ru/shareholders-and-investors/", "issuer_ir", "RAS")],
    "MTSS": [("ir", "https://ir.mts.ru/", "issuer_ir", "IFRS")],
    "TRNFP": [("ir", "https://www.transneft.ru/investors/", "issuer_ir", "IFRS/RAS")],
    "TATNP": [("ir", "https://www.tatneft.ru/aktsioneram-i-investoram/", "issuer_ir", "IFRS")],
    "PHOR": [("ir", "https://www.phosagro.com/investors/", "issuer_ir", "IFRS")],
    "MOEX": [
        ("ir", "https://www.moex.com/en/exchange/investors.aspx", "issuer_ir", "IFRS"),
        ("stat", "https://www.moex.com/s1355", "issuer_financial_statements", "IFRS"),
    ],
}
FAMILIES = {
    "X5": "EV/EBITDA; FCF; growth/store economics",
    "SBERP": "P/E; P/B-ROE; dividend; preferred discount",
    "LKOH": "P/E; EV/EBITDA; FCF/dividend yield; net debt",
    "LSNGP": "P/E; dividend formula; regulated capex/debt",
    "MTSS": "EV/OIBDA; FCF yield; leverage/dividend sustainability",
    "TRNFP": "P/E; EV/EBITDA; FCF/dividend yield; net debt",
    "TATNP": "P/E; EV/EBITDA; FCF/dividend yield; net debt",
    "PHOR": "cycle-normalized EV/EBITDA; FCF/dividend",
    "MOEX": "P/E; normalized fee/interest income; dividend",
}


def ensure_schema(con):
    con.execute(DDL)


def save_reconciliation(con):
    ensure_schema(con)
    cfg = parse_local_portfolio()
    latest = con.execute(
        "SELECT snapshot_id,total_value FROM portfolio_snapshots WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"status": "no_real_snapshot"}
    sid, equity = latest
    cash = cfg["cash"]
    con.execute(
        "INSERT OR REPLACE INTO portfolio_reconciliation VALUES (?,?,?,?,?,?,?,?,?,current_timestamp)",
        [
            sid,
            equity,
            cash,
            cfg.get("account_reference_value"),
            cfg.get("broker_reference_profit"),
            cfg.get("reconstructed_profit"),
            cfg.get("reconciliation_difference"),
            cfg.get("reconciliation_status"),
            True,
        ],
    )
    return {
        "snapshot_id": sid,
        "cash_position": cash,
        "equity_only": True,
        "status": cfg.get("reconciliation_status"),
    }


def discover_official_fundamentals(con):
    ensure_schema(con)
    rows = 0
    for secid, sources in SOURCES.items():
        for source_id, url, kind, standard in sources:
            con.execute(
                "INSERT OR REPLACE INTO issuer_official_sources VALUES (?,?,?,?,?,'discoverable',current_timestamp,?)",
                [
                    secid,
                    source_id,
                    url,
                    kind,
                    standard,
                    "Official source registered; values require document-level parser validation",
                ],
            )
            rows += 1
    return {
        "issuers": len(SOURCES),
        "sources": rows,
        "validated_values": con.execute(
            "SELECT count(*) FROM issuer_fundamental_values WHERE validation_status='validated'"
        ).fetchone()[0],
    }


def build_valuation_states(con):
    ensure_schema(con)
    sid = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    con.execute("DELETE FROM issuer_valuation_states WHERE snapshot_id=?", [sid])
    rows = 0
    for secid in SOURCES:
        count = con.execute(
            "SELECT count(*) FROM issuer_fundamental_values WHERE secid=? AND validation_status='validated'",
            [secid],
        ).fetchone()[0]
        status = "insufficient_data" if count < 3 else "experimental"
        limitation = (
            "No validated official point-in-time fundamental values; price range intentionally withheld"
            if not count
            else "Incomplete normalized history; range is not production-ready"
        )
        con.execute(
            "INSERT INTO issuer_valuation_states VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                sid,
                secid,
                FAMILIES[secid],
                None,
                None,
                None,
                None,
                None,
                None,
                "none" if not count else "low",
                "insufficient_data",
                None,
                limitation,
                status,
            ],
        )
        rows += 1
    return {"snapshot_id": sid, "rows": rows, "ranges_withheld": sum(1 for _ in SOURCES)}


def build_regime_risk(con):
    ensure_schema(con)
    sid = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    con.execute("DELETE FROM instrument_regime_risk WHERE snapshot_id=?", [sid])
    rows = 0
    for (secid,) in con.execute(
        "SELECT secid FROM portfolio_positions WHERE snapshot_id=?", [sid]
    ).fetchall():
        alpha = con.execute(
            "SELECT feature,status,regime_json FROM portfolio_alpha_validations WHERE secid=?", [secid]
        ).fetchone()
        factor, status, regimes = alpha if alpha else (None, "insufficient_history", "{}")
        latest = con.execute(
            "SELECT close FROM canonical_daily_prices WHERE canonical_secid=? ORDER BY trade_date DESC LIMIT 61",
            [secid],
        ).fetchall()
        p = np.array([x[0] for x in latest][::-1], float)
        vol = float(np.std(np.diff(np.log(p)), ddof=1) * np.sqrt(252)) if len(p) > 20 else math.nan
        dd = float(p[-1] / p.max() - 1) if len(p) else math.nan
        regime = "stress" if (vol > 0.35 or dd < -0.15) else "normal" if np.isfinite(vol) else "indeterminate"
        allowed = status in {"validated_candidate", "conditional_candidate"}
        effect = "conditional factor may inform timing" if allowed else "no positive factor evidence"
        con.execute(
            "INSERT INTO instrument_regime_risk VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                sid,
                secid,
                regime,
                "high" if vol > 0.35 else "normal",
                "deep" if dd < -0.15 else "normal",
                factor,
                status,
                None,
                ("normal" in regimes and "stress" in regimes),
                "low" if not allowed else "medium",
                effect,
                json.dumps({"volatility_60": vol, "drawdown_60": dd}),
            ],
        )
        rows += 1
    return {"snapshot_id": sid, "rows": rows}


def build_action_map(con):
    ensure_schema(con)
    sid = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    con.execute("DELETE FROM portfolio_action_map WHERE snapshot_id=?", [sid])
    rc = dict(
        (x[0].split(":", 1)[1], x[1])
        for x in con.execute(
            "SELECT factor,exposure FROM portfolio_factor_exposures WHERE snapshot_id=? AND factor LIKE 'risk_contribution:%'",
            [sid],
        ).fetchall()
    )
    rows = 0
    for secid, qty, avg, price, value, weight in con.execute(
        "SELECT secid,quantity,average_price,current_price,market_value,weight FROM portfolio_positions WHERE snapshot_id=?",
        [sid],
    ).fetchall():
        valuation = con.execute(
            "SELECT status FROM issuer_valuation_states WHERE snapshot_id=? AND secid=?", [sid, secid]
        ).fetchone()
        regime = con.execute(
            "SELECT regime,factor_status FROM instrument_regime_risk WHERE snapshot_id=? AND secid=?",
            [sid, secid],
        ).fetchone()
        div = con.execute(
            "SELECT count(*) FROM portfolio_dividend_outlook WHERE snapshot_id=? AND secid=?", [sid, secid]
        ).fetchone()[0]
        risk = rc.get(secid, 0)
        action = (
            "do not increase due to concentration"
            if weight >= 0.17 or risk >= 0.20
            else "insufficient data"
            if not valuation or valuation[0] == "insufficient_data"
            else "monitor"
        )
        tranche = 0.0 if action.startswith("do not") or action == "insufficient data" else 5.0
        against = ["target_not_set", "valuation evidence incomplete"]
        against += ["equity concentration"] if weight >= 0.17 else []
        con.execute(
            "INSERT INTO portfolio_action_map VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                sid,
                secid,
                price,
                qty,
                value,
                weight,
                risk,
                avg,
                (price - avg) * qty,
                valuation[0] if valuation else "insufficient_data",
                "estimated_not_announced" if div else "unavailable",
                regime[0] if regime else "indeterminate",
                "low",
                "concentrated" if weight >= 0.17 else "diversifier",
                "target_not_set",
                action,
                tranche,
                json.dumps([]),
                json.dumps(against),
                json.dumps(
                    [
                        "new official report",
                        "validated valuation becomes available",
                        "regime or concentration changes",
                    ]
                ),
            ],
        )
        rows += 1
    return {"snapshot_id": sid, "rows": rows}


def _alternative_period(con, sid, ids, current, period_type, value):
    panel = _panel(con, ids)
    cov = covariance_shrinkage(panel.to_numpy()) * 252
    methods = {
        "current": normalize_weights(current),
        "equal_weight": normalize_weights(np.ones(len(ids))),
        "inverse_volatility": inverse_volatility_weights(cov),
        "hrp": hierarchical_risk_parity(cov),
        "minimum_variance": minimum_variance_weights(cov),
        "maximum_diversification": maximum_diversification_weights(cov),
    }
    con.execute(
        "DELETE FROM portfolio_alternatives_v15 WHERE snapshot_id=? AND period_type=?", [sid, period_type]
    )
    for name, w in methods.items():
        r = panel.to_numpy() @ w
        rc = risk_contributions(cov, w)
        con.execute(
            "INSERT INTO portfolio_alternatives_v15 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                sid,
                period_type,
                panel.index.min(),
                panel.index.max(),
                name,
                json.dumps(dict(zip(ids, map(float, w), strict=True))),
                float(np.prod(1 + r) ** (252 / len(r)) - 1),
                annualized_volatility(r),
                downside_volatility(r),
                max_drawdown(r),
                json.dumps(dict(zip(ids, map(float, rc), strict=True))),
                float(np.abs(w - normalize_weights(current)).sum()),
                transaction_cost(normalize_weights(current), w, value),
                "Do not compare CAGR across period types",
            ],
        )


def build_alternatives(con):
    ensure_schema(con)
    sid, value = con.execute(
        "SELECT snapshot_id,total_value FROM portfolio_snapshots WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    pos = con.execute(
        "SELECT secid,market_value FROM portfolio_positions WHERE snapshot_id=? ORDER BY secid", [sid]
    ).fetchall()
    ids = [x[0] for x in pos]
    values = np.array([x[1] for x in pos])
    _alternative_period(con, sid, ids, values, "nine_stock_common_period", value)
    long_ids = [x for x in ids if x != "X5"]
    long_values = np.array([v for x, v in pos if x != "X5"])
    _alternative_period(con, sid, long_ids, long_values, "long_history_ex_x5", float(long_values.sum()))
    return {
        "snapshot_id": sid,
        "rows": con.execute(
            "SELECT count(*) FROM portfolio_alternatives_v15 WHERE snapshot_id=?", [sid]
        ).fetchone()[0],
    }


def save_intelligence_snapshot(con):
    ensure_schema(con)
    sid = con.execute(
        "SELECT snapshot_id FROM portfolio_snapshots WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    tables = {
        "positions": "portfolio_positions",
        "fundamentals": "issuer_official_sources",
        "valuations": "issuer_valuation_states",
        "dividends": "portfolio_dividend_outlook",
        "regimes": "instrument_regime_risk",
        "actions": "portfolio_action_map",
        "risk": "portfolio_risk_metrics",
    }
    payload = {
        k: con.execute(f"SELECT * FROM {t} WHERE snapshot_id=?", [sid]).fetchall()
        if k != "fundamentals"
        else con.execute(
            "SELECT secid,source_id,source_url,source_type,reporting_standard,status,notes "
            "FROM issuer_official_sources ORDER BY secid,source_id"
        ).fetchall()
        for k, t in tables.items()
    }
    digest = hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()
    iid = digest[:24]
    before = con.execute(
        "SELECT count(*) FROM portfolio_intelligence_shadow WHERE intelligence_id=?", [iid]
    ).fetchone()[0]
    con.execute(
        "INSERT OR IGNORE INTO portfolio_intelligence_shadow VALUES (?,current_timestamp,current_date,?,?,?,?,?,?,?,?,?,?,?)",
        [
            iid,
            sid,
            *[
                json.dumps(payload[k], default=str)
                for k in (
                    "positions",
                    "fundamentals",
                    "valuations",
                    "dividends",
                    "regimes",
                    "actions",
                    "risk",
                )
            ],
            json.dumps({"intelligence": VERSION}),
            digest,
            True,
        ],
    )
    return {"inserted": 0 if before else 1, "intelligence_id": iid}


def intelligence_status(con):
    ensure_schema(con)
    return {
        "official_sources": con.execute("SELECT count(*) FROM issuer_official_sources").fetchone()[0],
        "validated_fundamentals": con.execute(
            "SELECT count(*) FROM issuer_fundamental_values WHERE validation_status='validated'"
        ).fetchone()[0],
        "valuations": dict(
            con.execute("SELECT status,count(*) FROM issuer_valuation_states GROUP BY 1").fetchall()
        ),
        "actions": dict(
            con.execute("SELECT allowed_action,count(*) FROM portfolio_action_map GROUP BY 1").fetchall()
        ),
        "snapshots": con.execute("SELECT count(*) FROM portfolio_intelligence_shadow").fetchone()[0],
    }


def run_intelligence(con):
    from .portfolio_v14 import build_portfolio_dividend_outlook, calculate_real_portfolio

    result = {
        "portfolio": calculate_real_portfolio(con),
        "reconciliation": save_reconciliation(con),
        "fundamentals": discover_official_fundamentals(con),
        "valuations": build_valuation_states(con),
        "dividends": build_portfolio_dividend_outlook(con),
        "regimes": build_regime_risk(con),
        "actions": build_action_map(con),
        "alternatives": build_alternatives(con),
        "snapshot": save_intelligence_snapshot(con),
    }
    result["status"] = intelligence_status(con)
    return result


def backfill_official_fundamentals(con):
    """Populate generic fundamentals from official, hashed, explicitly mapped sources."""
    from moex_analytics.fundamentals import generic

    discovered = discover_official_fundamentals(con)
    results = {"sources": discovered, "SBER": generic.migrate_sber(con)}
    for issuer in ("MOEX", "MTSS", "PHOR"):
        try:
            results[issuer] = generic.import_official_html(con, issuer)
        except Exception as exc:  # preserve a truthful source-level failure
            results[issuer] = {"status": "source_access_problem", "error": str(exc)}
    try:
        results["X5"] = generic.import_x5_workbook(con)
    except Exception as exc:  # preserve a truthful source-level failure
        results["X5"] = {"status": "source_access_problem", "error": str(exc)}
    results["coverage"] = con.execute(
        "SELECT issuer,validated_values,status FROM issuer_fundamental_coverage ORDER BY issuer"
    ).fetchall()
    return results
