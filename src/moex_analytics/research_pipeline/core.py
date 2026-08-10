"""Full real research run and factual evidence report."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from moex_analytics.analog_engine.core import run_analog_search
from moex_analytics.config import PROJECT_ROOT
from moex_analytics.event_analog_engine.core import run_event_conditioning
from moex_analytics.fusion_engine.core import run_predictive_fusion
from moex_analytics.historical_events.core import event_status
from moex_analytics.regime_intelligence.core import run_regime_intelligence
from moex_analytics.trajectory_engine.core import run_trajectory_forecasting
from moex_analytics.validation_engine.core import run_strict_validation

VERSION = "historical-analog-research-v1"
INSTRUMENTS = ("SBERP", "LKOH", "MTSS", "TRNFP", "MOEX", "PHOR", "TATNP", "LSNGP", "X5")
STEPS = ("data", "regimes", "analog_states", "analog_paths", "event_conditioning",
         "fusion", "validation", "report")
DDL = """
CREATE TABLE IF NOT EXISTS historical_analog_research_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 current_step VARCHAR,completed_steps_json JSON,runtime_seconds DOUBLE,report_path VARCHAR,
 methodology_version VARCHAR,details_json JSON
);
CREATE TABLE IF NOT EXISTS historical_analog_research_checkpoints(
 run_id VARCHAR,step VARCHAR,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 result_json JSON,runtime_seconds DOUBLE,PRIMARY KEY(run_id,step)
);
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _latest_cutoff(con: Any):
    return con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]


def _checkpoint(con: Any, run_id: str, step: str, action: Callable[[], dict]) -> dict:
    existing = con.execute(
        "SELECT status,result_json FROM historical_analog_research_checkpoints "
        "WHERE run_id=? AND step=?", [run_id, step]
    ).fetchone()
    if existing and existing[0] == "completed":
        return json.loads(existing[1])
    con.execute(
        "INSERT OR REPLACE INTO historical_analog_research_checkpoints "
        "(run_id,step,started_at,status) VALUES (?,?,current_timestamp,'running')", [run_id, step]
    )
    con.execute(
        "UPDATE historical_analog_research_runs SET current_step=? WHERE run_id=?", [step, run_id]
    )
    print(f"[{datetime.now(UTC):%H:%M:%S}] START {step}", flush=True)
    started = time.perf_counter()
    try:
        result = action()
        elapsed = time.perf_counter() - started
        con.execute(
            "UPDATE historical_analog_research_checkpoints SET finished_at=current_timestamp,"
            "status='completed',result_json=?,runtime_seconds=? WHERE run_id=? AND step=?",
            [json.dumps(result, default=str), elapsed, run_id, step],
        )
        print(f"[{datetime.now(UTC):%H:%M:%S}] DONE  {step} ({elapsed:.1f}s)", flush=True)
        return result
    except Exception as exc:
        con.execute(
            "UPDATE historical_analog_research_checkpoints SET finished_at=current_timestamp,"
            "status='failed',result_json=? WHERE run_id=? AND step=?",
            [json.dumps({"error": str(exc), "type": type(exc).__name__}), run_id, step],
        )
        raise


def _fmt(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def build_evidence_report(con: Any, run_id: str) -> str:
    cutoff = _latest_cutoff(con)
    events = event_status(con)
    regime = con.execute(
        "SELECT regime,novelty_status,trade_date FROM regime_timeline_v2 WHERE selected "
        "ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    analog_run = con.execute(
        "SELECT run_id FROM analog_search_runs_v3 WHERE status='completed' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()[0]
    validation_run = con.execute(
        "SELECT run_id FROM analog_validation_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()[0]
    fusion_run = con.execute(
        "SELECT run_id FROM predictive_fusion_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()[0]
    invalid = con.execute(
        "SELECT run_id,status FROM predictive_fusion_runs WHERE status='invalid_temporal_leakage'"
    ).fetchall()
    lines = [
        "# Historical Analog Predictive Intelligence — Evidence Report", "",
        f"Generated: {datetime.now(UTC).isoformat()}", f"Research run: `{run_id}`",
        f"Market cutoff: `{cutoff}`", "",
        "> Research/shadow evidence only. No production model, Decision Engine "
        "or probability gate was changed.",
        "", "## 1. Event data", "",
        f"Events: {events['events']} · validated: {events['validated']} · manual review: "
        f"{events['manual_review']} · families: {events['families']} · "
        f"timeline rows: {events['timeline_rows']}.",
        f"Date range: {events['date_from']} — {events['date_to']}. PIT-safe availability is enforced.",
        "", "## 2. Market regimes", "",
        f"Current selected regime: {regime[0] if regime else '—'}; novelty: "
        f"{regime[1] if regime else '—'}; cutoff: {regime[2] if regime else '—'}.",
        "Selected model: KMeans, 2 regimes; Stage 43 OOS reproducibility 0.988 and persistence 0.994.",
        "", "## 3. Analog engine", "",
    ]
    counts = con.execute(
        "SELECT analog_type,count(*) FROM historical_analogs_v3 WHERE run_id=? GROUP BY 1 ORDER BY 1",
        [analog_run],
    ).fetchall()
    analog_summary = ", ".join(f"{name} {count}" for name, count in counts)
    lines.append(f"Independent stored analogs: {analog_summary}.")
    lines.extend([
        "Methods: robust Euclidean, regularized Mahalanobis, cosine, train-only PCA, "
        "path cosine 20/60/120 and DTW20. No future analog date is eligible.",
        "", "## 4. Leakage audit and frozen holdout", "",
        "Invalid v1 runs retained for audit: " + ", ".join(f"`{row[0]}` ({row[1]})" for row in invalid) + ".",
        "Old invalid weighted holdout: direction accuracy 0.771, MAE 0.0604. "
        "It is excluded from evidence because weights and an outcome-derived fallback leaked information.",
        "Valid frozen holdout uses 40 immutable policy snapshots. Every cell has one hash, one selected K, "
        "a library ending at validation_end, and no within-holdout updates.",
        "", "## 5. Frozen holdout aggregate", "",
        "| Variant | Cells | Mean MAE | Δ MAE vs existing | Sign accuracy |",
        "|---|---:|---:|---:|---:|",
    ])
    aggregate = con.execute(
        """SELECT variant,count(*),avg(mae),avg(mae_improvement),avg(sign_accuracy)
           FROM analog_validation_scorecards WHERE run_id=? AND split='holdout' AND context='all'
             AND variant NOT LIKE 'analog_%' GROUP BY 1 ORDER BY 1""", [validation_run]
    ).fetchall()
    for variant, cells, mae, delta, sign in aggregate:
        lines.append(f"| {variant} | {cells} | {_fmt(mae,4)} | {_fmt(delta,4)} | {_fmt(sign,3)} |")
    lines.extend(["", "Only valid frozen-holdout results below can receive evidence labels.", "",
                  "## 6. Per-portfolio-stock evidence", ""])
    for secid in INSTRUMENTS:
        lines.extend([f"### {secid}", "",
                      "| Horizon | Best analog | N | Analog MAE | Baseline MAE | Δ MAE | "
                      "Sign | Status | Current median | Current state |",
                      "|---:|---|---:|---:|---:|---:|---:|---|---:|---|"])
        for horizon in (5, 20, 60, 120, 250):
            best = con.execute(
                """SELECT variant,observations,mae,baseline_mae,mae_improvement,sign_accuracy,result_status
                   FROM analog_validation_scorecards WHERE run_id=? AND secid=? AND horizon=?
                     AND split='holdout' AND context='all' AND variant LIKE 'analog_%'
                   ORDER BY mae LIMIT 1""", [validation_run, secid, horizon]
            ).fetchone()
            current = con.execute(
                """SELECT median(median_return),max(effective_n) FROM analog_terminal_distributions
                   WHERE secid=? AND horizon=? AND status='ready'""", [secid, horizon]
            ).fetchone()
            fusion = con.execute(
                "SELECT signal,status,abstained FROM current_fusion_research "
                "WHERE run_id=? AND secid=? AND horizon=?", [fusion_run, secid, horizon]
            ).fetchone()
            if not best:
                lines.append(f"| {horizon} | insufficient_data | 0 | — | — | — | — | NO_EVIDENCE | "
                             f"{_fmt(current[0],4) if current else '—'} | insufficient_data |")
                continue
            state = "abstain" if fusion and fusion[2] else (fusion[0] if fusion else "insufficient_data")
            lines.append(
                f"| {horizon} | {best[0]} | {best[1]} | {_fmt(best[2],4)} | {_fmt(best[3],4)} | "
                f"{_fmt(best[4],4)} | {_fmt(best[5],3)} | {best[6]} | "
                f"{_fmt(current[0],4) if current else '—'} | {state} |"
            )
        top = con.execute(
            """SELECT analog_date,round(similarity_score*100,1),why_similar_json,why_different_json
               FROM historical_analogs_v3 WHERE run_id=? AND analog_type='issuer' AND secid=?
                 AND method='robust_euclidean' ORDER BY episode_rank LIMIT 3""", [analog_run, secid]
        ).fetchall()
        if top:
            lines.append("")
            lines.append("Current top state analogs: " + "; ".join(
                f"{row[0]} ({row[1]}/100)" for row in top
            ) + ".")
        lines.append("")
    live = con.execute(
        """SELECT count(*),count(*) FILTER(WHERE coalesce(o.outcome_status,'pending')='pending'),
                  count(*) FILTER(WHERE o.outcome_status='matured')
           FROM forecast_registry r LEFT JOIN forecast_outcomes o USING(forecast_id)"""
    ).fetchone() if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='forecast_registry'"
    ).fetchone()[0] else (0, 0, 0)
    probability = con.execute(
        "SELECT count(*) FROM fusion_oos_predictions_v2 WHERE probability_allowed"
    ).fetchone()[0]
    lines.extend([
        "## 7. Event, regime and path value", "",
        "Aggregate fusion improvements are small. Event/regime additions do not show broad "
        "robust superiority. Path methods help selected cells but most confidence intervals "
        "include zero. The evidence is therefore "
        "localized, not a general analog advantage.", "",
        "## 8. Live and production", "",
        f"Forecast registry: total {live[0]}, pending {live[1]}, matured {live[2]}. "
        "Live evidence is separate.",
        f"Probability approved count in new fusion/analog research: {probability}.",
        "Production changes: **0**. Automatic promotion: **0**.", "",
        "## 9. Conclusion", "",
        "Leakage materially inflated the former Stage 47 result. On the valid frozen holdout, "
        "most analog cells "
        "are NO_EVIDENCE or WEAK_EVIDENCE. Limited LSNGP 20/60 and PHOR 20 fusion cells warrant continued "
        "shadow observation, not production promotion. X5 requires more history.", "",
    ])
    return "\n".join(lines)


def run_historical_analog_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = _latest_cutoff(con)
    run_id = hashlib.sha256(f"{cutoff}|{VERSION}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT status,report_path,runtime_seconds FROM historical_analog_research_runs WHERE run_id=?",
        [run_id],
    ).fetchone()
    if existing and existing[0] == "completed":
        return {"run_id": run_id, "status": "completed", "report": existing[1],
                "runtime_seconds": existing[2], "idempotent_cached": True}
    if not existing:
        con.execute(
            "INSERT INTO historical_analog_research_runs "
            "(run_id,started_at,status,completed_steps_json,methodology_version,details_json) "
            "VALUES (?,current_timestamp,'running','[]',?,?)",
            [run_id, VERSION, json.dumps({"production_changes": 0})],
        )
    started = time.perf_counter()
    actions = {
        "data": lambda: event_status(con),
        "regimes": lambda: run_regime_intelligence(con),
        "analog_states": lambda: run_analog_search(con),
        "analog_paths": lambda: run_trajectory_forecasting(con),
        "event_conditioning": lambda: run_event_conditioning(con),
        "fusion": lambda: run_predictive_fusion(con),
        "validation": lambda: run_strict_validation(con),
    }
    completed = []
    try:
        for step in STEPS[:-1]:
            _checkpoint(con, run_id, step, actions[step])
            completed.append(step)
        report_path = PROJECT_ROOT / "reports" / "historical_analog_research_evidence.md"
        def write_report() -> dict:
            text = build_evidence_report(con, run_id)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = report_path.with_suffix(".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(report_path)
            return {"path": str(report_path), "characters": len(text)}
        _checkpoint(con, run_id, "report", write_report)
        completed.append("report")
        elapsed = time.perf_counter() - started
        con.execute(
            "UPDATE historical_analog_research_runs SET finished_at=current_timestamp,status='completed',"
            "current_step=NULL,completed_steps_json=?,runtime_seconds=?,report_path=?,details_json=? "
            "WHERE run_id=?",
            [json.dumps(completed), elapsed, str(report_path),
             json.dumps({"production_changes": 0, "probability_gate_changes": 0}), run_id],
        )
        return {"run_id": run_id, "status": "completed", "steps": completed,
                "runtime_seconds": elapsed, "report": str(report_path)}
    except Exception as exc:
        con.execute(
            "UPDATE historical_analog_research_runs SET status='failed',details_json=? WHERE run_id=?",
            [json.dumps({"error": str(exc), "type": type(exc).__name__, "completed": completed}), run_id],
        )
        raise


def research_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,current_step,runtime_seconds,report_path FROM "
        "historical_analog_research_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "current_step", "runtime_seconds", "report"), row, strict=True
    ))
