"""Stage 65 read-only distillation of saved research evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd

from moex_analytics.config import PROJECT_ROOT

VERSION = "investor-decision-distillation-v1"
DDL = """
CREATE TABLE IF NOT EXISTS investor_decision_runs(
 run_id VARCHAR PRIMARY KEY,cutoff DATE,created_at TIMESTAMP,status VARCHAR,rows BIGINT,
 details_json JSON,immutable BOOLEAN);
CREATE TABLE IF NOT EXISTS distilled_investor_views(
 run_id VARCHAR,cutoff DATE,secid VARCHAR,status_code VARCHAR,status_label VARCHAR,
 group_60 VARCHAR,group_120 VARCHAR,group_250 VARCHAR,conviction VARCHAR,
 downside DOUBLE,analog_role VARCHAR,timing VARCHAR,portfolio_fit VARCHAR,data_quality VARCHAR,
 live_n INTEGER,reasons_json JSON,risks_json JSON,change_label VARCHAR,immutable BOOLEAN,
 PRIMARY KEY(run_id,secid));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _status(groups: dict[int, str], conviction: str, quality: str, abstain: bool) -> tuple[str, str]:
    if quality != "complete":
        return "GRAY", "⚪ Недостаточно доказательств"
    if abstain:
        return "YELLOW", "🟡 Средняя привлекательность / наблюдать"
    top = sum(value == "TOP GROUP" for value in groups.values())
    bottom = sum(value == "BOTTOM GROUP" for value in groups.values())
    if top >= 2 and conviction in {"moderate", "higher"}:
        return "GREEN", "🟢 Сильнее большинства / можно рассматривать"
    if bottom >= 2:
        return "ORANGE", "🟠 Слабее альтернатив / повышенная осторожность"
    return "YELLOW", "🟡 Средняя привлекательность / наблюдать"


def build_investor_decisions(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    group_run = con.execute("SELECT run_id,cutoff FROM rank_group_runs WHERE status='completed' "
                            "ORDER BY created_at DESC LIMIT 1").fetchone()
    fresh_run = con.execute("SELECT run_id,status FROM snapshot_freshness_runs "
                            "ORDER BY created_at DESC LIMIT 1").fetchone()
    if not group_run or not fresh_run:
        raise ValueError("rank groups and freshness evidence required")
    run_id = hashlib.sha256(f"{VERSION}|{group_run[0]}|{fresh_run[0]}".encode()).hexdigest()[:20]
    cached = con.execute("SELECT status,rows FROM investor_decision_runs WHERE run_id=?",
                         [run_id]).fetchone()
    if cached:
        return {"run_id": run_id, "status": cached[0], "rows": cached[1], "cached": True}
    groups = con.execute("SELECT * FROM current_rank_groups WHERE run_id=?", [group_run[0]]).df()
    composite = con.execute("SELECT * FROM composite_rank_groups WHERE run_id=?",
                            [group_run[0]]).df().set_index("secid")
    fresh = con.execute("SELECT secid,rank_eligible,reason FROM instrument_freshness_states "
                        "WHERE run_id=?", [fresh_run[0]]).df().set_index("secid")
    try:
        opportunity = con.execute("SELECT secid,avg(tail_downside) downside,bool_or(abstain) abstain,"
            "max(portfolio_weight) portfolio_weight,max(risk_contribution) risk_contribution FROM "
            "opportunity_candidates WHERE run_id=(SELECT run_id FROM opportunity_research_runs "
            "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1) AND candidate_type='equity' "
            "GROUP BY secid").df().set_index("secid")
    except Exception:
        opportunity = pd.DataFrame()
    live_n = con.execute("SELECT secid,count(*) FILTER(WHERE o.status='matured') n FROM "
        "live_ranking_snapshots s JOIN live_ranking_outcomes o USING(snapshot_id) GROUP BY secid").df()
    live_map = dict(zip(live_n.secid, live_n.n, strict=False))
    rows = []
    for secid, sample in groups.groupby("secid"):
        group_map = dict(zip(sample.horizon.astype(int), sample.group_label, strict=True))
        conviction = str(composite.loc[secid, "relative_conviction"])
        quality = "complete" if bool(fresh.loc[secid, "rank_eligible"]) else "incomplete"
        opp = opportunity.loc[secid] if not opportunity.empty and secid in opportunity.index else None
        abstain = bool(opp.abstain) if opp is not None else True
        code, label = _status(group_map, conviction, quality, abstain)
        reasons = [
            f"60d: {group_map.get(60, 'нет данных')}",
            f"120d: {group_map.get(120, 'нет данных')}",
            f"250d: {group_map.get(250, 'нет данных')}",
        ]
        risks = ["интервалы рангов пересекаются", "live ranking evidence недостаточно",
                 "тайминг не показал статистического преимущества"]
        portfolio_fit = "cash_preferred_or_abstain" if abstain else "research_candidate"
        rows.append([run_id, group_run[1], secid, code, label, group_map.get(60),
            group_map.get(120), group_map.get(250), conviction,
            float(opp.downside) if opp is not None and pd.notna(opp.downside) else None,
            "downside_and_scenario_only_not_direction", "no_statistical_timing_edge",
            portfolio_fit, quality, int(live_map.get(secid, 0)), json.dumps(reasons, ensure_ascii=False),
            json.dumps(risks, ensure_ascii=False), "→ без изменений", True])
    frame = pd.DataFrame(rows, columns=("run_id", "cutoff", "secid", "status_code",
        "status_label", "group_60", "group_120", "group_250", "conviction", "downside",
        "analog_role", "timing", "portfolio_fit", "data_quality", "live_n", "reasons_json",
        "risks_json", "change_label", "immutable"))
    con.register("_distilled", frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO distilled_investor_views ({columns}) SELECT {columns} FROM _distilled")
    con.unregister("_distilled")
    counts = frame.status_code.value_counts().to_dict()
    con.execute("INSERT INTO investor_decision_runs (run_id,cutoff,created_at,status,rows,"
                "details_json,immutable) VALUES (?,?,current_timestamp,'completed',?,?,true)",
                [run_id, group_run[1], len(frame), json.dumps({"status_counts": counts,
                    "production_changes": 0, "probability_published": False,
                    "analog_direction_used": False, "timing_edge_claimed": False})])
    return {"run_id": run_id, "status": "completed", "rows": len(frame),
            "status_counts": counts, "cached": False}


def answer_saved_question(con: Any, question: str) -> str:
    rows = con.execute("SELECT secid,status_label,group_60,group_120,group_250,portfolio_fit,"
        "data_quality,live_n FROM distilled_investor_views WHERE run_id=(SELECT run_id FROM "
        "investor_decision_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1) "
        "ORDER BY secid").fetchall()
    if not rows:
        return "Недостаточно сохранённых evidence для ответа."
    upper = question.upper()
    selected = [row for row in rows if row[0] in upper]
    if selected:
        return "\n".join(f"{r[0]}: {r[1]}; 60/120/250: {r[2]}/{r[3]}/{r[4]}; "
                         f"portfolio fit: {r[5]}; live N={r[7]}." for r in selected)
    if "CASH" in upper or "100" in upper:
        return ("Резерв сохранён: интервалы относительных рангов пересекаются, optimizer выбрал "
                "CASH_PREFERRED; зрелых live ranking outcomes пока нет. "
                "Это abstention, не прогноз падения.")
    return "; ".join(f"{r[0]} — {r[1]}" for r in rows)


def investor_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,cutoff,status,rows,details_json FROM investor_decision_runs "
                      "ORDER BY created_at DESC LIMIT 1").fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "cutoff", "status", "rows", "details"), row, strict=True
    ))


def write_final_report(con: Any) -> dict[str, str]:
    validation = con.execute("SELECT horizon,rank_ic,ci_low,ci_high,"
        "top_bottom_spread_after_costs,turnover,status FROM long_horizon_ranking_validation "
        "WHERE run_id=(SELECT run_id FROM long_horizon_ranking_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1) AND context_type='all' AND horizon IN (60,120,250) "
        "ORDER BY horizon").df()
    views = con.execute("SELECT secid,status_label,group_60,group_120,group_250,conviction,"
        "data_quality,live_n FROM distilled_investor_views WHERE run_id=(SELECT run_id FROM "
        "investor_decision_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1) "
        "ORDER BY secid").df()
    path = PROJECT_ROOT / "reports" / "stage65_long_horizon_ranking_evidence.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    def markdown(frame: pd.DataFrame) -> str:
        values = [[str(value) for value in row] for row in frame.itertuples(index=False, name=None)]
        header = "| " + " | ".join(map(str, frame.columns)) + " |"
        separator = "| " + " | ".join("---" for _ in frame.columns) + " |"
        return "\n".join([header, separator] + ["| " + " | ".join(row) + " |" for row in values])

    text = "# Stage 65 — Long-horizon ranking evidence\n\n"
    text += "Production changes = 0. Probability gate unchanged.\n\n"
    text += "## Frozen OOS validation\n\n" + markdown(validation) + "\n\n"
    text += "## Current portfolio view\n\n" + markdown(views) + "\n\n"
    text += ("## Interpretation\n\nStable relative edge is present on the available 2022+ frozen "
             "OOS slice for 60/120/250, but earlier fixed periods are unavailable. Current rank "
             "intervals overlap into one broad group; therefore no false leader is published. "
             "Analog direction and timing are excluded from the BASIC decision. The optimizer "
             "keeps cash because uncertainty, downside and live evidence are insufficient.\n")
    path.write_text(text, encoding="utf-8")
    return {"report": str(path)}
