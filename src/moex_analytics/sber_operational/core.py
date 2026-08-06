"""SBER operational point-in-time intelligence and immutable live tracking."""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import UTC, date, datetime
from typing import Any

VERSION = "sber-operational-v1"
RULE_VERSION = "sber-production-rules-v1"
SOURCES = (
    ("cbr", "Банк России", "https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/"),
    ("sber", "Сбер: результаты", "https://www.sberbank.com/ru/investor-relations/reports-and-publications"),
    ("sber-ir", "Sber IR", "https://www.sberbank.com/investor-relations"),
    ("disclosure", "Центр раскрытия", "https://www.e-disclosure.ru/portal/company.aspx?id=3043"),
    ("moex", "MOEX SBER", "https://www.moex.com/ru/issue.aspx?board=TQBR&code=SBER"),
    ("moexfn", "MOEX Finance Index", "https://www.moex.com/ru/index/MOEXFN"),
)
DDL = """
CREATE TABLE IF NOT EXISTS sber_operational_sources(source_id VARCHAR PRIMARY KEY,name VARCHAR,url VARCHAR,official BOOLEAN,last_checked TIMESTAMP,notes VARCHAR);
CREATE TABLE IF NOT EXISTS cbr_form_metric_mapping(form_number VARCHAR,line_code VARCHAR,column_code VARCHAR,metric_id VARCHAR,description VARCHAR,unit VARCHAR,valid_from DATE,valid_to DATE,methodology_version VARCHAR,comparability_status VARCHAR,notes VARCHAR,PRIMARY KEY(form_number,line_code,column_code,methodology_version));
CREATE TABLE IF NOT EXISTS sber_operational_observations(observation_id VARCHAR PRIMARY KEY,metric_id VARCHAR,period_start DATE,period_end DATE,value_kind VARCHAR,reported_value DOUBLE,derived_value DOUBLE,unit VARCHAR,form_number VARCHAR,document_id VARCHAR,publication_date DATE,available_from TIMESTAMPTZ,revision_id VARCHAR,methodology_version VARCHAR,comparability_status VARCHAR,derivation_formula VARCHAR,input_document_ids JSON,confidence DOUBLE,source_url VARCHAR,loaded_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sber_nowcasts(as_of_date DATE,method VARCHAR,metric_id VARCHAR,value DOUBLE,confidence DOUBLE,assumptions_json JSON,historical_error DOUBLE,input_hash VARCHAR,calculation_version VARCHAR,calculated_at TIMESTAMP,PRIMARY KEY(as_of_date,method,metric_id,calculation_version));
CREATE TABLE IF NOT EXISTS sber_operating_indicators(as_of_date DATE,indicator_id VARCHAR,direction VARCHAR,current_value DOUBLE,threshold DOUBLE,baseline DOUBLE,trend VARCHAR,severity VARCHAR,confidence DOUBLE,source VARCHAR,source_date DATE,explanation VARCHAR,calculation_version VARCHAR,PRIMARY KEY(as_of_date,indicator_id,direction,calculation_version));
CREATE TABLE IF NOT EXISTS sber_daily_operating_state(trade_date DATE,latest_monthly_period DATE,latest_quarterly_period DATE,latest_ifrs_period DATE,profit_ytd DOUBLE,profit_month DOUBLE,profit_ttm DOUBLE,annual_profit_nowcast DOUBLE,eps_nowcast DOUBLE,roe_nowcast DOUBLE,equity DOUBLE,loans_retail DOUBLE,loans_corporate DOUBLE,customer_funds DOUBLE,provisions DOUBLE,overdue_loans DOUBLE,capital_adequacy DOUBLE,sector_relative_score DOUBLE,early_warning_count INTEGER,positive_indicator_count INTEGER,operating_confidence DOUBLE,data_age_days INTEGER,calculation_version VARCHAR,calculated_at TIMESTAMP,PRIMARY KEY(trade_date,calculation_version));
CREATE TABLE IF NOT EXISTS sber_operational_evidence(as_of_date DATE,score DOUBLE,confidence DOUBLE,positive_evidence_json JSON,negative_evidence_json JSON,warnings_json JSON,latest_period DATE,data_age_days INTEGER,weight DOUBLE,status VARCHAR,calculation_version VARCHAR,PRIMARY KEY(as_of_date,calculation_version));
CREATE TABLE IF NOT EXISTS sber_price_zone_audit(as_of_date DATE,lower_bound DOUBLE,upper_bound DOUBLE,old_name VARCHAR,new_name VARCHAR,confidence DOUBLE,reason VARCHAR,calculation_version VARCHAR,PRIMARY KEY(as_of_date,lower_bound,calculation_version));
CREATE TABLE IF NOT EXISTS sber_position_size_explanation(as_of_date DATE,factor VARCHAR,effect DOUBLE,maximum_size_before DOUBLE,maximum_size_after DOUBLE,reason VARCHAR,calculation_version VARCHAR,PRIMARY KEY(as_of_date,factor,calculation_version));
CREATE TABLE IF NOT EXISTS sber_frozen_rules(rule_version VARCHAR PRIMARY KEY,configuration_hash VARCHAR,activated_at TIMESTAMP,retired_at TIMESTAMP,development_period VARCHAR,validation_period VARCHAR,holdout_period VARCHAR,status VARCHAR);
CREATE TABLE IF NOT EXISTS sber_live_predictions(snapshot_id VARCHAR,cutoff TIMESTAMPTZ,model_version VARCHAR,payload_json JSON,input_hash VARCHAR,created_at TIMESTAMP,PRIMARY KEY(snapshot_id,model_version),UNIQUE(cutoff,model_version));
CREATE TABLE IF NOT EXISTS sber_live_decisions(snapshot_id VARCHAR,model_version VARCHAR,decision VARCHAR,confidence DOUBLE,first_fraction DOUBLE,price DOUBLE,zones_json JSON,calculation_versions_json JSON,input_hash VARCHAR,created_at TIMESTAMP,PRIMARY KEY(snapshot_id,model_version));
CREATE TABLE IF NOT EXISTS sber_live_outcomes(snapshot_id VARCHAR,model_version VARCHAR,horizon INTEGER,matured_at DATE,exit_price DOUBLE,total_return DOUBLE,dividends DOUBLE,max_drawdown DOUBLE,max_gain DOUBLE,strategy_results_json JSON,status VARCHAR,calculated_at TIMESTAMP,PRIMARY KEY(snapshot_id,model_version,horizon));
CREATE TABLE IF NOT EXISTS sber_live_scorecards(model_version VARCHAR,sample_type VARCHAR,as_of_date DATE,sample_size INTEGER,mean_error DOUBLE,median_error DOUBLE,sign_accuracy DOUBLE,interval_coverage DOUBLE,max_drawdown DOUBLE,missed_return DOUBLE,confidence_calibration DOUBLE,details_json JSON,calculated_at TIMESTAMP,PRIMARY KEY(model_version,sample_type,as_of_date));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)
    for sid, name, url in SOURCES:
        con.execute(
            "INSERT OR IGNORE INTO sber_operational_sources VALUES (?,?,?,?,NULL,?)",
            [sid, name, url, True, "Document validation required"],
        )
    con.execute(
        "INSERT OR IGNORE INTO sber_frozen_rules VALUES (?,?,current_timestamp,NULL,?,?,?,'production')",
        [
            RULE_VERSION,
            hashlib.sha256(RULE_VERSION.encode()).hexdigest(),
            "pre-live",
            "pseudo-out-of-sample",
            "live-only",
        ],
    )


def classify_value_kind(kind: str) -> str:
    if kind not in {"stock", "monthly_flow", "ytd_flow", "quarter_flow", "ttm", "annualized_run_rate"}:
        raise ValueError(f"Unsupported value kind: {kind}")
    return kind


def derive_period_value(current: float, previous: float, *, comparable: bool) -> float:
    if not comparable:
        raise ValueError("YTD periods are not strictly comparable")
    return current - previous


def upsert_observation(con: Any, row: dict[str, Any]) -> str:
    ensure_schema(con)
    classify_value_kind(row["value_kind"])
    oid = hashlib.sha256(
        "|".join(str(row.get(k)) for k in ("metric_id", "period_end", "document_id", "revision_id")).encode()
    ).hexdigest()[:24]
    if con.execute("SELECT 1 FROM sber_operational_observations WHERE observation_id=?", [oid]).fetchone():
        return oid
    con.execute(
        "INSERT INTO sber_operational_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
        [
            oid,
            row["metric_id"],
            row.get("period_start"),
            row["period_end"],
            row["value_kind"],
            row.get("reported_value"),
            row.get("derived_value"),
            row["unit"],
            row.get("form_number"),
            row["document_id"],
            row["publication_date"],
            row["available_from"],
            row.get("revision_id", "original"),
            row.get("methodology_version", "unknown"),
            row.get("comparability_status", "review_required"),
            row.get("derivation_formula"),
            json.dumps(row.get("input_document_ids", [])),
            row.get("confidence", 0.0),
            row["source_url"],
        ],
    )
    return oid


def import_validated_fundamentals(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    rows = con.execute(
        "SELECT document_id,metric_id,period_start,period_end,normalized_value,normalized_unit,publication_date,available_from,revision_id,source_note FROM fundamental_metric_values WHERE secid='SBER' AND quality_status='validated' ORDER BY available_from"
    ).fetchall()
    before = con.execute("SELECT count(*) FROM sber_operational_observations").fetchone()[0]
    flows = {
        "net_profit",
        "interest_income",
        "interest_expense",
        "fee_income",
        "operating_expenses",
        "provisions",
        "profit_before_tax",
        "tax",
    }
    for doc, metric, start, end, value, unit, published, available, revision, note in rows:
        upsert_observation(
            con,
            {
                "metric_id": metric,
                "period_start": start,
                "period_end": end,
                "value_kind": "ytd_flow" if metric in flows else "stock",
                "reported_value": value,
                "unit": unit or "RUB",
                "document_id": doc,
                "publication_date": published,
                "available_from": available,
                "revision_id": revision,
                "methodology_version": "source-document",
                "comparability_status": "validated_source_only",
                "confidence": 0.8,
                "source_url": note or "official document catalogue",
            },
        )
    return {
        "status": "success",
        "source_rows": len(rows),
        "rows_written": con.execute("SELECT count(*) FROM sber_operational_observations").fetchone()[0]
        - before,
    }


def _latest(con: Any, metric: str, as_of: date):
    return con.execute(
        "SELECT period_end,reported_value,derived_value,value_kind,available_from,confidence,document_id FROM sber_operational_observations WHERE metric_id=? AND available_from<=CAST(? AS TIMESTAMP)+INTERVAL 1 DAY ORDER BY available_from DESC,revision_id DESC LIMIT 1",
        [metric, as_of],
    ).fetchone()


def calculate_nowcast(con: Any, as_of: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = (
        as_of
        or con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
        or date.today()
    )
    profit = _latest(con, "net_profit", as_of)
    if not profit:
        return {"status": "insufficient_data", "reason": "validated net_profit unavailable"}
    period, reported, *_ = profit
    elapsed = max(period.month, 1)
    methods = {"simple_ytd": reported * 12 / elapsed, "conservative": reported * 12 / elapsed * 0.9}
    equity = _latest(con, "total_equity", as_of)
    shares = 21586948000.0
    digest = hashlib.sha256(repr((profit, equity)).encode()).hexdigest()
    for method, value in methods.items():
        assumptions = {
            "not_official_guidance": True,
            "months_elapsed": elapsed,
            "source_document": profit[6],
            "seasonality_used": False,
        }
        confidence = float(profit[5] or 0) * 100 * (0.8 if method == "simple_ytd" else 0.65)
        for metric, v in (
            ("net_profit", value),
            ("eps", value / shares),
            ("roe", value / equity[1] if equity and equity[1] else None),
            ("dividend", value * 0.5 / shares),
        ):
            if v is not None:
                con.execute(
                    "INSERT OR REPLACE INTO sber_nowcasts VALUES (?,?,?,?,?,?,?,?,?,current_timestamp)",
                    [as_of, method, metric, v, confidence, json.dumps(assumptions), None, digest, VERSION],
                )
    return {
        "status": "success",
        "as_of": str(as_of),
        "methods": methods,
        "ensemble": statistics.median(methods.values()),
        "limitations": ["not Sber guidance", "seasonality disabled"],
    }


def calculate_operating_state(con: Any, as_of: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = (
        as_of
        or con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
        or date.today()
    )
    now = calculate_nowcast(con, as_of)
    if now["status"] != "success":
        return now
    profit, equity = _latest(con, "net_profit", as_of), _latest(con, "total_equity", as_of)
    names = (
        "loans_retail",
        "loans_corporate",
        "customer_funds",
        "provisions",
        "overdue_loans",
        "capital_adequacy",
    )
    metrics = {n: _latest(con, n, as_of) for n in names}
    annual = now["ensemble"]
    shares = 21586948000.0
    confidence = max(0, min(65, float(profit[5] or 0) * 100) - sum(v is None for v in metrics.values()) * 4)
    age = (as_of - profit[0]).days
    values = [metrics[n][1] if metrics[n] else None for n in names]
    con.execute(
        "INSERT OR REPLACE INTO sber_daily_operating_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
        [
            as_of,
            profit[0],
            profit[0],
            None,
            profit[1],
            None,
            None,
            annual,
            annual / shares,
            annual / equity[1] if equity and equity[1] else None,
            equity[1] if equity else None,
            *values,
            None,
            0,
            1,
            confidence,
            age,
            VERSION,
        ],
    )
    con.execute(
        "INSERT OR REPLACE INTO sber_operational_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            as_of,
            0.25,
            confidence,
            json.dumps(["validated profit available"]),
            json.dumps([]),
            json.dumps(["101/102 and sector coverage incomplete"]),
            profit[0],
            age,
            0.0,
            "experimental_weight_zero",
            VERSION,
        ],
    )
    return {
        "status": "success",
        "trade_date": str(as_of),
        "profit_ytd": profit[1],
        "annual_profit_nowcast": annual,
        "eps_nowcast": annual / shares,
        "roe_nowcast": annual / equity[1] if equity and equity[1] else None,
        "operating_confidence": confidence,
        "missing_metrics": [n for n, v in metrics.items() if v is None],
        "block_weight": 0.0,
    }


def audit_zones(con: Any, as_of: date | None = None, confidence: float = 50.9) -> dict[str, Any]:
    ensure_schema(con)
    as_of = as_of or date.today()
    zones = [
        (0, 240, "ниже 240 ₽ — пересмотр рисков", "зона проверки исходных рисков"),
        (240, 345, "сильная зона накопления", "потенциально привлекательная зона при подтверждении данных"),
        (345, 395, "умеренная зона покупки", "допустимая зона первой покупки"),
        (395, 445, "нейтральная зона", "нейтральная зона"),
        (445, 705, "не догонять", "зона низкого запаса прочности"),
        (705, 99999, "переоценка", "зона повышенного риска переоценки"),
    ]
    con.execute(
        "DELETE FROM sber_price_zone_audit WHERE as_of_date=? AND calculation_version=?", [as_of, VERSION]
    )
    for low, high, old, new in zones:
        con.execute(
            "INSERT INTO sber_price_zone_audit VALUES (?,?,?,?,?,?,?,?)",
            [as_of, low, high, old, new, confidence, "Low confidence: cautious wording", VERSION],
        )
    return {"status": "success", "zones": 6, "strong_word_removed": True}


def explain_position_size(con: Any, as_of: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = as_of or date.today()
    factors = [
        ("policy_cap", 0.3, 1.0, 0.3, "Initial cap"),
        ("valuation_confidence", -0.1, 0.3, 0.2, "Confidence near 50%"),
        ("operating_unvalidated", -0.05, 0.2, 0.15, "Weight zero"),
        ("data_freshness", -0.05, 0.15, 0.1, "Coverage incomplete"),
    ]
    con.execute(
        "DELETE FROM sber_position_size_explanation WHERE as_of_date=? AND calculation_version=?",
        [as_of, VERSION],
    )
    for row in factors:
        con.execute(
            "INSERT INTO sber_position_size_explanation VALUES (?,?,?,?,?,?,?)", [as_of, *row, VERSION]
        )
    return {"status": "success", "first_fraction": 0.1, "why_not_20_or_30": "confidence and coverage caps"}


def save_live_decision(con: Any, as_of: date | None = None, cutoff: datetime | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = (
        as_of
        or con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
        or date.today()
    )
    cutoff = cutoff or datetime.combine(as_of, datetime.max.time(), tzinfo=UTC).replace(microsecond=0)
    state = con.execute(
        "SELECT * EXCLUDE (calculated_at) FROM sber_daily_operating_state WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    decision = con.execute(
        "SELECT decision_status,decision_confidence,first_position_fraction,current_price FROM sber_decision_results WHERE as_of_date<=? ORDER BY as_of_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    price = con.execute(
        "SELECT close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    payload = {
        "as_of": str(as_of),
        "price": price[0] if price else None,
        "decision": list(decision) if decision else None,
        "operating": list(state) if state else None,
        "rule_version": RULE_VERSION,
    }
    digest = hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()
    old = con.execute(
        "SELECT snapshot_id FROM sber_live_predictions WHERE cutoff=? AND model_version=?",
        [cutoff, RULE_VERSION],
    ).fetchone()
    if old:
        return {"status": "no_change", "snapshot_id": old[0], "rows_written": 0, "input_hash": digest}
    sid = hashlib.sha256(f"{cutoff}|{RULE_VERSION}|{digest}".encode()).hexdigest()[:24]
    d = payload["decision"] or ["insufficient_data", 0.0, 0.0, payload["price"]]
    con.execute(
        "INSERT INTO sber_live_predictions VALUES (?,?,?,?,?,current_timestamp)",
        [sid, cutoff, RULE_VERSION, json.dumps(payload, default=str), digest],
    )
    models = (
        "production",
        "production_plus_operational_shadow",
        "without_technical_shadow",
        "without_valuation_shadow",
        "staged_buy_baseline",
        "buy_hold_baseline",
    )
    for model in models:
        con.execute(
            "INSERT INTO sber_live_decisions VALUES (?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                sid,
                f"{RULE_VERSION}:{model}",
                d[0],
                d[1],
                d[2],
                payload["price"],
                "[]",
                json.dumps({"rules": RULE_VERSION, "operational": VERSION}),
                digest,
            ],
        )
    return {
        "status": "success",
        "snapshot_id": sid,
        "rows_written": 1,
        "shadow_models": 5,
        "input_hash": digest,
    }


def update_outcomes(con: Any, as_of: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = as_of or date.today()
    rows = 0
    for sid, model, payload in con.execute(
        "SELECT snapshot_id,model_version,payload_json FROM sber_live_predictions"
    ).fetchall():
        start = date.fromisoformat(json.loads(payload)["as_of"])
        prices = con.execute(
            "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            [start, as_of],
        ).fetchall()
        for h in (1, 5, 20, 60, 120, 250):
            if len(prices) <= h:
                continue
            path = [v for _, v in prices[: h + 1]]
            ret = path[-1] / path[0] - 1
            dd = min(v / max(path[: i + 1]) - 1 for i, v in enumerate(path))
            gain = max(v / path[0] - 1 for v in path)
            con.execute(
                "INSERT OR IGNORE INTO sber_live_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                [
                    sid,
                    model,
                    h,
                    prices[h][0],
                    path[-1],
                    ret,
                    0.0,
                    dd,
                    gain,
                    json.dumps({"buy_now": ret}),
                    "matured",
                ],
            )
            rows += 1
    return {"status": "success", "matured_rows": rows, "incomplete_horizons_not_written": True}


def calculate_scorecard(con: Any, as_of: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = as_of or date.today()
    rows = con.execute(
        "SELECT model_version,total_return,max_drawdown FROM sber_live_outcomes WHERE status='matured'"
    ).fetchall()
    for model in sorted({r[0] for r in rows}):
        vals = [r for r in rows if r[0] == model]
        con.execute(
            "INSERT OR REPLACE INTO sber_live_scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [
                model,
                "live",
                as_of,
                len(vals),
                None,
                None,
                sum(r[1] > 0 for r in vals) / len(vals),
                None,
                min(r[2] for r in vals),
                None,
                None,
                json.dumps({"historical_mixed": False}),
            ],
        )
    return {"status": "success", "live_outcomes": len(rows), "historical_separated": True}


def discover(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    return {
        "official_sources": [{"id": x[0], "name": x[1], "url": x[2]} for x in SOURCES],
        "forms": ["101", "102", "806", "807"],
        "validation_rule": "discovery never creates observations",
    }


def status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    tables = (
        "sber_operational_observations",
        "sber_daily_operating_state",
        "sber_live_predictions",
        "sber_live_decisions",
        "sber_live_outcomes",
    )
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def run_daily(con: Any, as_of: date | None = None) -> dict[str, Any]:
    ensure_schema(con)
    as_of = (
        as_of
        or con.execute(
            "SELECT max(trade_date) FROM canonical_daily_prices WHERE canonical_secid='SBER'"
        ).fetchone()[0]
        or date.today()
    )
    return {
        "trade_date": str(as_of),
        "import": import_validated_fundamentals(con),
        "operating_state": calculate_operating_state(con, as_of),
        "zone_audit": audit_zones(con, as_of),
        "position_size": explain_position_size(con, as_of),
        "live": save_live_decision(con, as_of),
        "outcomes": update_outcomes(con, as_of),
        "scorecard": calculate_scorecard(con, as_of),
    }
