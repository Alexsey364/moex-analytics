"""Deterministic, human-friendly portfolio intelligence built from stored evidence only."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np

VERSION = "human-portfolio-intelligence-v1"
HORIZONS = (1, 5, 20, 60, 120, 250)
ISSUER_MAP = {"SBERP": "SBER", "LSNGP": "LSNG", "TATNP": "TATN"}
CONFIDENCE_LABELS = ((75, "высокая"), (60, "выше средней"), (40, "средняя"), (0, "низкая"))
BLOCKS = (
    "business_quality",
    "valuation",
    "dividend_outlook",
    "technical_state",
    "market_regime",
    "relative_strength",
    "volatility_risk",
    "drawdown_risk",
    "macro_rate_context",
    "sector_context",
    "portfolio_fit",
    "data_quality",
    "research_signal",
    "event_risk",
)

DDL = """
CREATE TABLE IF NOT EXISTS human_intelligence_blocks(
 report_id VARCHAR,secid VARCHAR,block_id VARCHAR,score DOUBLE,confidence DOUBLE,status VARCHAR,
 evidence_for_json JSON,evidence_against_json JSON,freshness_days INTEGER,source_count INTEGER,
 methodology_version VARCHAR,PRIMARY KEY(report_id,secid,block_id));
CREATE TABLE IF NOT EXISTS human_horizon_views(
 report_id VARCHAR,secid VARCHAR,horizon INTEGER,status VARCHAR,view_text VARCHAR,
 evidence_json JSON,confidence DOUBLE,methodology_version VARCHAR,
 PRIMARY KEY(report_id,secid,horizon));
CREATE TABLE IF NOT EXISTS human_instrument_synthesis(
 report_id VARCHAR,secid VARCHAR,current_price DOUBLE,quantity DOUBLE,average_price DOUBLE,
 equity_weight DOUBLE,profit_loss_pct DOUBLE,short_term_view VARCHAR,medium_term_view VARCHAR,
 long_term_view VARCHAR,valuation_view VARCHAR,dividend_view VARCHAR,risk_view VARCHAR,
 portfolio_view VARCHAR,timing_view VARCHAR,action_group VARCHAR,confidence_label VARCHAR,
 confidence_score DOUBLE,top_positive VARCHAR,top_negative VARCHAR,evidence_for_json JSON,
 evidence_against_json JSON,invalidation_json JSON,data_status VARCHAR,
 PRIMARY KEY(report_id,secid));
CREATE TABLE IF NOT EXISTS human_daily_reports(
 report_id VARCHAR PRIMARY KEY,analysis_cutoff DATE,created_at TIMESTAMP,portfolio_snapshot_id VARCHAR,
 total_value DOUBLE,total_profit_pct DOUBLE,market_regime VARCHAR,data_freshness_days INTEGER,
 stale_warning VARCHAR,input_hash VARCHAR,methodology_version VARCHAR,immutable BOOLEAN);
"""


@dataclass(frozen=True)
class Confidence:
    coverage: float
    freshness: float
    fundamentals: float
    oos_validation: float
    regime_stability: float
    agreement: float
    sample_size: float
    structural_breaks: float

    @property
    def score(self) -> float:
        weights = (0.18, 0.14, 0.14, 0.16, 0.10, 0.12, 0.08, 0.08)
        return round(float(sum(v * w for v, w in zip(asdict(self).values(), weights, strict=True))), 1)

    @property
    def label(self) -> str:
        return next(label for threshold, label in CONFIDENCE_LABELS if self.score >= threshold)


def ensure_schema(con) -> None:
    con.execute(DDL)


def confidence_engine(*, coverage, freshness_days, validated, alpha, regime, agreement, sample, breaks):
    """Explainable confidence decomposition; this is not a forecast probability."""
    freshness = max(0.0, 100.0 - min(max(freshness_days, 0), 500) / 5)
    return Confidence(
        min(100.0, coverage),
        freshness,
        85.0 if validated >= 5 else 20.0,
        75.0 if alpha in {"validated_candidate", "conditional_candidate"} else 25.0,
        70.0 if regime not in {"indeterminate", "unknown"} else 20.0,
        min(100.0, max(0.0, agreement)),
        min(100.0, math.sqrt(max(sample, 0)) * 6),
        25.0 if breaks else 80.0,
    )


def horizon_status(momentum: float | None, volatility: float | None) -> tuple[str, str]:
    """Convert trailing technical state to restrained language, never to probability."""
    if momentum is None or not np.isfinite(momentum):
        return "unknown", "? недостаточно данных"
    threshold = max(0.015, (volatility or 0.2) * math.sqrt(1 / 252) * 0.75)
    if momentum > threshold:
        return "small_positive", "↑ небольшой позитивный перевес"
    if momentum < -threshold:
        return "small_negative", "↓ небольшой негативный перевес"
    return "neutral", "→ нейтрально"


def _latest_snapshot(con):
    return con.execute(
        "SELECT snapshot_id,as_of_date,total_value FROM portfolio_snapshots "
        "WHERE status='real' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()


def _issuer(secid: str) -> str:
    return ISSUER_MAP.get(secid, secid)


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _instrument_inputs(con, snapshot_id: str, secid: str) -> dict:
    pos = con.execute(
        "SELECT quantity,average_price,current_price,market_value,weight FROM portfolio_positions "
        "WHERE snapshot_id=? AND secid=?",
        [snapshot_id, secid],
    ).fetchone()
    prices = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? "
        "ORDER BY trade_date DESC LIMIT 251",
        [secid],
    ).fetchall()
    values = np.array([row[1] for row in prices][::-1], float)
    latest_date = prices[0][0] if prices else None
    returns = np.diff(np.log(values)) if len(values) > 1 else np.array([])
    vol = float(np.std(returns[-60:], ddof=1) * np.sqrt(252)) if len(returns) >= 20 else None
    momentum = {h: float(values[-1] / values[-h - 1] - 1) if len(values) > h else None for h in HORIZONS}
    issuer = _issuer(secid)
    fundamental = con.execute(
        "SELECT validated_values,data_age_days,confidence,status,documents_found "
        "FROM issuer_fundamental_coverage WHERE issuer=?",
        [issuer],
    ).fetchone()
    valuation = con.execute(
        "SELECT status,zone,confidence,limitations FROM issuer_valuation_states "
        "WHERE snapshot_id=? AND secid=?",
        [snapshot_id, secid],
    ).fetchone()
    regime = con.execute(
        "SELECT regime,volatility_state,drawdown_state,factor_status,sign_switch_warning,evidence_json "
        "FROM instrument_regime_risk WHERE snapshot_id=? AND secid=?",
        [snapshot_id, secid],
    ).fetchone()
    alpha = con.execute(
        "SELECT status,effective_n,folds_stable,folds,regime_json FROM portfolio_alpha_validations "
        "WHERE secid=? AND horizon=20 ORDER BY run_id DESC LIMIT 1",
        [secid],
    ).fetchone()
    risk = con.execute(
        "SELECT exposure FROM portfolio_factor_exposures WHERE snapshot_id=? AND factor=?",
        [snapshot_id, f"risk_contribution:{secid}"],
    ).fetchone()
    dividends = con.execute(
        "SELECT scenario,month,net,dps,yield_current,status,confidence FROM portfolio_dividend_outlook "
        "WHERE snapshot_id=? AND secid=? ORDER BY month,scenario",
        [snapshot_id, secid],
    ).fetchall()
    action = con.execute(
        "SELECT allowed_action,evidence_for_json,evidence_against_json,invalidation_triggers_json "
        "FROM portfolio_action_map WHERE snapshot_id=? AND secid=?",
        [snapshot_id, secid],
    ).fetchone()
    return {
        "position": pos,
        "latest_date": latest_date,
        "volatility": vol,
        "momentum": momentum,
        "fundamental": fundamental,
        "valuation": valuation,
        "regime": regime,
        "alpha": alpha,
        "risk_contribution": risk[0] if risk else 0.0,
        "dividends": dividends,
        "action": action,
    }


def _block(score, confidence, status, positive=(), negative=(), freshness=None, sources=0):
    return {
        "score": max(0, min(100, float(score))),
        "confidence": max(0, min(100, float(confidence))),
        "status": status,
        "for": list(positive),
        "against": list(negative),
        "freshness": freshness,
        "sources": int(sources),
    }


def _build_blocks(data: dict) -> dict:
    pos = data["position"]
    fund = data["fundamental"] or (0, None, "none", "insufficient_official_data", 0)
    validated, age, fund_conf, fund_status, documents = fund
    val = data["valuation"] or ("insufficient_data", "insufficient_data", "none", "Нет модели")
    regime = data["regime"] or ("indeterminate", "unknown", "unknown", "insufficient_history", False, "{}")
    alpha = data["alpha"] or ("insufficient_history", 0, 0, 0, "{}")
    vol = data["volatility"]
    m20 = data["momentum"][20]
    rc = float(data["risk_contribution"] or 0)
    concentration = pos[4] if pos else 0
    fresh = age if age is not None else 999
    return {
        "business_quality": _block(
            65 if validated >= 5 else 25,
            80 if validated >= 5 else 20,
            "validated" if validated >= 5 else "insufficient_data",
            [f"Подтверждено показателей: {validated}"] if validated else [],
            [] if validated else ["Недостаточно подтверждённых фундаментальных данных"],
            fresh,
            documents,
        ),
        "valuation": _block(
            50, 25 if val[0] == "experimental" else 10, val[0], [], [val[3]], fresh, documents
        ),
        "dividend_outlook": _block(
            55 if data["dividends"] else 20,
            35 if data["dividends"] else 10,
            "estimated_not_announced" if data["dividends"] else "unavailable",
            [],
            ["Оценки не являются объявленными дивидендами"] if data["dividends"] else ["Нет расчёта"],
            fresh,
            1,
        ),
        "technical_state": _block(
            60 if (m20 or 0) > 0 else 40,
            55,
            horizon_status(m20, vol)[0],
            ["Положительная динамика за 20 сессий"] if (m20 or 0) > 0 else [],
            ["Отрицательная динамика за 20 сессий"] if (m20 or 0) < 0 else [],
            0,
            1,
        ),
        "market_regime": _block(
            35 if regime[0] == "stress" else 55,
            55,
            regime[0],
            [],
            ["Стрессовый рыночный режим"] if regime[0] == "stress" else [],
            0,
            1,
        ),
        "relative_strength": _block(
            50, 35, "neutral", [], ["Нет отдельного подтверждённого преимущества"], 0, 1
        ),
        "volatility_risk": _block(
            30 if (vol or 0) > 0.35 else 60,
            70 if vol else 20,
            "high" if (vol or 0) > 0.35 else "normal",
            [],
            ["Повышенная историческая волатильность"] if (vol or 0) > 0.35 else [],
            0,
            1,
        ),
        "drawdown_risk": _block(
            35 if regime[2] == "deep" else 60,
            60,
            regime[2],
            [],
            ["Глубокая текущая просадка"] if regime[2] == "deep" else [],
            0,
            1,
        ),
        "macro_rate_context": _block(
            50, 25, "insufficient_data", [], ["Нет issuer-specific validated связи"], 0, 0
        ),
        "sector_context": _block(50, 25, "insufficient_data", [], ["Секторный вывод не подтверждён"], 0, 0),
        "portfolio_fit": _block(
            25 if concentration >= 0.17 or rc >= 0.20 else 65,
            80,
            "concentrated" if concentration >= 0.17 or rc >= 0.20 else "diversifier",
            ["Умеренный вклад в риск"] if rc < 0.20 else [],
            ["Высокая концентрация или вклад в риск"] if concentration >= 0.17 or rc >= 0.20 else [],
            0,
            1,
        ),
        "data_quality": _block(
            min(100, validated * 8 + 25),
            80,
            fund_status,
            [f"Официальных документов: {documents}"] if documents else [],
            ["Данные устарели"] if fresh > 180 else [],
            fresh,
            documents,
        ),
        "research_signal": _block(
            55 if alpha[0] == "conditional_candidate" else 35,
            55 if alpha[0] == "conditional_candidate" else 25,
            alpha[0],
            ["Есть условный OOS-кандидат"] if alpha[0] == "conditional_candidate" else [],
            ["Research signal не подтверждён для production"] if alpha[0] != "validated_candidate" else [],
            0,
            1,
        ),
        "event_risk": _block(50, 15, "unknown", [], ["Календарь событий неполон"], 0, 0),
    }


def _synthesis(data: dict, blocks: dict) -> dict:
    pos = data["position"]
    statuses = {h: horizon_status(data["momentum"][h], data["volatility"])[1] for h in HORIZONS}
    positive = [item for block in blocks.values() for item in block["for"]]
    negative = [item for block in blocks.values() for item in block["against"]]
    alpha = data["alpha"] or ("insufficient_history", 0, 0, 0, "{}")
    fund = data["fundamental"] or (0, 999, "none", "insufficient_official_data", 0)
    regime = data["regime"] or ("indeterminate", "unknown", "unknown", "insufficient_history", False, "{}")
    signs = [1 if "позитивный" in statuses[h] else -1 if "негативный" in statuses[h] else 0 for h in HORIZONS]
    agreement = 100 - 25 * len(set(signs))
    confidence = confidence_engine(
        coverage=blocks["data_quality"]["score"],
        freshness_days=fund[1] or 999,
        validated=fund[0],
        alpha=alpha[0],
        regime=regime[0],
        agreement=agreement,
        sample=alpha[1] or 0,
        breaks=bool(regime[4]),
    )
    concentration = pos[4] >= 0.17 or data["risk_contribution"] >= 0.20
    missing = fund[0] < 5
    if concentration:
        action_group, timing = "do_not_increase", "Не увеличивать из-за концентрации"
    elif missing:
        action_group, timing = "insufficient_data", "Недостаточно данных"
    elif regime[0] == "stress" or "негативный" in statuses[20]:
        action_group, timing = "wait", "Разумно подождать"
    else:
        action_group, timing = "consider", "Допустим небольшой поэтапный набор"
    valuation = "Цена пока не оценена фундаментально"
    dividend = (
        "Есть только оценочный сценарий, дивиденд не объявлен"
        if data["dividends"]
        else "Недостаточно данных о дивиденде"
    )
    risk = (
        "Повышенный риск" if regime[0] == "stress" or (data["volatility"] or 0) > 0.35 else "Риск умеренный"
    )
    long_view = "Фундаментально нейтральна" if fund[0] >= 5 else "Недостаточно фундаментальных данных"
    return {
        "horizons": statuses,
        "short": statuses[5],
        "medium": statuses[20],
        "long": long_view,
        "valuation": valuation,
        "dividend": dividend,
        "risk": risk,
        "portfolio": "Не увеличивать из-за концентрации"
        if concentration
        else "Вес не создаёт критической концентрации",
        "timing": timing,
        "group": action_group,
        "confidence": confidence,
        "positive": positive[:5] or ["Подтверждённого положительного evidence нет"],
        "negative": negative[:5] or ["Явного отрицательного evidence нет"],
        "invalidation": _json(data["action"][3], []) if data["action"] else ["Обновление данных"],
        "data_status": fund[3],
    }


def build_daily_report(con, analysis_cutoff: date | None = None) -> dict:
    ensure_schema(con)
    snapshot = _latest_snapshot(con)
    if not snapshot:
        return {"status": "insufficient_data", "reason": "real portfolio snapshot missing"}
    sid, snapshot_date, total = snapshot
    cutoff = (
        analysis_cutoff or con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    )
    secids = [
        r[0]
        for r in con.execute(
            "SELECT secid FROM portfolio_positions WHERE snapshot_id=? ORDER BY secid", [sid]
        ).fetchall()
    ]
    payload = []
    for secid in secids:
        data = _instrument_inputs(con, sid, secid)
        blocks = _build_blocks(data)
        synthesis = _synthesis(data, blocks)
        payload.append((secid, data, blocks, synthesis))
    digest = hashlib.sha256(
        json.dumps(
            [(s, b, {k: v for k, v in y.items() if k != "confidence"}) for s, _, b, y in payload],
            default=str,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    report_id = digest[:24]
    existing = con.execute(
        "SELECT count(*) FROM human_daily_reports WHERE report_id=?", [report_id]
    ).fetchone()[0]
    if not existing:
        latest_dates = [d["latest_date"] for _, d, _, _ in payload if d["latest_date"]]
        freshness = max((cutoff - min(latest_dates)).days, 0) if latest_dates else 999
        regimes = [d["regime"][0] for _, d, _, _ in payload if d["regime"]]
        market_regime = "стрессовый" if regimes.count("stress") > len(regimes) / 2 else "смешанный"
        stale = "Данные могут быть устаревшими" if freshness > 3 else ""
        costs = sum((d["position"][1] or 0) * d["position"][0] for _, d, _, _ in payload)
        pnl = (total / costs - 1) if costs else None
        con.execute(
            "INSERT INTO human_daily_reports VALUES (?, ?,current_timestamp,?,?,?,?,?,?,?, ?,TRUE)",
            [report_id, cutoff, sid, total, pnl, market_regime, freshness, stale, digest, VERSION],
        )
        for secid, data, blocks, synthesis in payload:
            for block_id, block in blocks.items():
                con.execute(
                    "INSERT INTO human_intelligence_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        report_id,
                        secid,
                        block_id,
                        block["score"],
                        block["confidence"],
                        block["status"],
                        json.dumps(block["for"], ensure_ascii=False),
                        json.dumps(block["against"], ensure_ascii=False),
                        block["freshness"],
                        block["sources"],
                        VERSION,
                    ],
                )
            for horizon, view in synthesis["horizons"].items():
                con.execute(
                    "INSERT INTO human_horizon_views VALUES (?,?,?,?,?,?,?,?)",
                    [
                        report_id,
                        secid,
                        horizon,
                        view[0],
                        view,
                        json.dumps({"trailing_return": data["momentum"][horizon]}),
                        synthesis["confidence"].score,
                        VERSION,
                    ],
                )
            pos = data["position"]
            pnl = (pos[2] / pos[1] - 1) if pos[1] else None
            con.execute(
                "INSERT INTO human_instrument_synthesis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    report_id,
                    secid,
                    pos[2],
                    pos[0],
                    pos[1],
                    pos[4],
                    pnl,
                    synthesis["short"],
                    synthesis["medium"],
                    synthesis["long"],
                    synthesis["valuation"],
                    synthesis["dividend"],
                    synthesis["risk"],
                    synthesis["portfolio"],
                    synthesis["timing"],
                    synthesis["group"],
                    synthesis["confidence"].label,
                    synthesis["confidence"].score,
                    synthesis["positive"][0],
                    synthesis["negative"][0],
                    json.dumps(synthesis["positive"], ensure_ascii=False),
                    json.dumps(synthesis["negative"], ensure_ascii=False),
                    json.dumps(synthesis["invalidation"], ensure_ascii=False),
                    synthesis["data_status"],
                ],
            )
    return {
        "status": "completed",
        "report_id": report_id,
        "inserted": 0 if existing else 1,
        "analysis_cutoff": cutoff,
        "positions": len(payload),
    }


def latest_report(con) -> dict:
    row = con.execute("SELECT * FROM human_daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return {"status": "insufficient_data"}
    columns = [d[0] for d in con.description]
    return dict(zip(columns, row, strict=True))


INTENTS = {
    "Почему SBERP жёлтый?": "trace:SBERP",
    "Какие данные ты использовал по Лукойлу?": "trace:LKOH",
    "Почему программа не советует увеличивать MTSS?": "trace:MTSS",
    "Что мешает X5 стать зелёным?": "trace:X5",
    "Каких данных не хватает по TATNP?": "trace:TATNP",
    "Что сейчас лучше докупить?": "consider",
    "Что сейчас можно пополнить?": "consider",
    "Что лучше не увеличивать?": "do_not_increase",
    "Что самое рискованное?": "risk",
    "Какая позиция самая рискованная?": "risk",
    "Где самый высокий риск?": "risk",
    "Где самая высокая дивидендная доходность?": "dividend",
    "Где лучший дивиденд?": "dividend",
    "Какие ближайшие дивиденды?": "dividend",
    "Какие дивиденды ожидаются?": "dividend",
    "Что по Сберу?": "SBERP",
    "Что программа думает о SBERP?": "SBERP",
    "Что по Лукойлу?": "LKOH",
    "Что по LKOH на ближайшую неделю?": "LKOH",
    "Что по X5 на год?": "X5",
    "Где слишком большая концентрация?": "do_not_increase",
    "Что будет при IMOEX −15%?": "scenario",
    "Что будет с портфелем при падении IMOEX?": "scenario",
    "Почему сейчас лучше ждать?": "wait",
    "Почему программа предлагает ждать?": "wait",
    "Почему программа допускает накопление?": "consider",
}


def answer_question(con, question: str) -> dict:
    """Route a supported question to stored results; never generates facts."""
    intent = INTENTS.get(question.strip())
    if intent and intent.startswith("trace:"):
        from moex_analytics.transparency import explain_current_decision

        trace = explain_current_decision(con, intent.split(":", 1)[1])
        excluded = [f"{item['block']}: {item['reason']}" for item in trace["excluded"]]
        return {
            "conclusion": f"{trace['secid']}: {trace['final_status']}",
            "supporting_evidence": trace["summary"]["positive"],
            "opposing_evidence": trace["summary"]["negative"] + excluded[:5],
            "confidence": "низкая" if "live" in trace["summary"]["main_limitation"] else "средняя",
            "data_cutoff": trace["cutoff"],
            "supported": True,
        }
    report = latest_report(con)
    cutoff = report.get("analysis_cutoff")
    if not intent or report.get("status") == "insufficient_data":
        return {
            "conclusion": "Для этого вывода пока недостаточно данных",
            "supporting_evidence": [],
            "opposing_evidence": [],
            "confidence": "низкая",
            "data_cutoff": cutoff,
            "supported": False,
        }
    report_id = report["report_id"]
    base = "SELECT secid,short_term_view,medium_term_view,long_term_view,timing_view,action_group,confidence_label,evidence_for_json,evidence_against_json FROM human_instrument_synthesis WHERE report_id=?"
    rows = con.execute(base, [report_id]).fetchall()
    selected = []
    if intent in {"consider", "wait", "do_not_increase"}:
        selected = [r for r in rows if r[5] == intent]
        conclusion = ", ".join(r[0] for r in selected) or "Для этого вывода пока недостаточно данных"
    elif intent == "risk":
        selected = sorted(rows, key=lambda r: r[4] != "Не увеличивать из-за концентрации")[:1]
        conclusion = (
            f"Наибольшее ограничение по риску: {selected[0][0]}"
            if selected
            else "Для этого вывода пока недостаточно данных"
        )
    elif intent == "dividend":
        div = con.execute(
            "SELECT secid,month,net,status,confidence FROM portfolio_dividend_outlook WHERE snapshot_id=? AND scenario='base' ORDER BY month,net DESC",
            [report["portfolio_snapshot_id"]],
        ).fetchall()
        conclusion = (
            "; ".join(f"{s}: {m}, оценочно {n:.2f} ₽ ({st})" for s, m, n, st, _ in div)
            or "Для этого вывода пока недостаточно данных"
        )
        return {
            "conclusion": conclusion,
            "supporting_evidence": ["Только сохранённый base-сценарий"],
            "opposing_evidence": ["ESTIMATED не равен CONFIRMED"],
            "confidence": "низкая",
            "data_cutoff": cutoff,
            "supported": bool(div),
        }
    elif intent == "scenario":
        vals = con.execute(
            "SELECT secid,mechanical_sensitivity,confidence FROM portfolio_scenarios_v2 WHERE snapshot_id=? AND scenario='IMOEX_minus_15' ORDER BY mechanical_sensitivity",
            [report["portfolio_snapshot_id"]],
        ).fetchall()
        conclusion = (
            "; ".join(f"{s}: механический сценарий {v:.1%}" for s, v, _ in vals)
            or "Для этого вывода пока недостаточно данных"
        )
        return {
            "conclusion": conclusion,
            "supporting_evidence": ["Историческая beta и block bootstrap"],
            "opposing_evidence": ["Сценарий не является прогнозом"],
            "confidence": "средняя" if vals else "низкая",
            "data_cutoff": cutoff,
            "supported": bool(vals),
        }
    else:
        selected = [r for r in rows if r[0] == intent]
        conclusion = (
            f"{intent}: {selected[0][4]}. Месяц: {selected[0][2]}."
            if selected
            else "Для этого вывода пока недостаточно данных"
        )
    evidence_for = [x for r in selected for x in _json(r[7], [])][:5]
    evidence_against = [x for r in selected for x in _json(r[8], [])][:5]
    confidence = selected[0][6] if selected else "низкая"
    return {
        "conclusion": conclusion,
        "supporting_evidence": evidence_for,
        "opposing_evidence": evidence_against,
        "confidence": confidence,
        "data_cutoff": cutoff,
        "supported": bool(selected),
    }


def run_daily_intelligence(con, *, update_data: bool = True) -> dict:
    """Run daily pipeline, preserving a usable stale report when external updates fail."""
    warnings = []
    if update_data:
        latest_market_date = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
        stale_days = (date.today() - latest_market_date).days if latest_market_date else 999
        if stale_days > 3:
            try:
                from .core import build_portfolio_total_returns, download_portfolio_history

                download_portfolio_history(con)
                build_portfolio_total_returns(con)
            except Exception as exc:  # network/source failure must not block dashboard
                warnings.append(f"Обновление данных не завершено: {type(exc).__name__}: {exc}")
    try:
        from .intelligence import run_intelligence

        run_intelligence(con)
    except Exception as exc:
        warnings.append(f"Часть расчётов использует предыдущие данные: {type(exc).__name__}: {exc}")
    report = build_daily_report(con)
    report["warnings"] = warnings
    report["message"] = "Анализ завершён.\nОткройте:\nhttp://localhost:8501"
    return report
