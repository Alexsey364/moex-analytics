"""Transparent temporal fusion of independent research evidence blocks."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from moex_analytics.analog_engine.core import INSTRUMENTS

from .schema import DDL

VERSION = "predictive-fusion-v1"
HORIZONS = (5, 20, 60, 120, 250)
BLOCKS = ("baseline", "technical", "pooled", "regime", "sector", "macro", "fundamental",
          "valuation", "analog", "event_analog", "meta_confidence")
MIN_WEIGHT_HISTORY = 100
HOLDOUT_FRACTION = 0.20


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def disagreement_score(values: list[float]) -> float:
    clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    if len(clean) < 2:
        return 0.0
    return float(np.std(np.sign(clean)) / 1.0)


def performance_weights(history: pd.DataFrame, blocks: list[str]) -> dict[str, float]:
    """Weights derive exclusively from prior errors supplied by the caller."""
    if len(history) < MIN_WEIGHT_HISTORY:
        return {block: 1 / len(blocks) for block in blocks}
    inverse = {}
    for block in blocks:
        error = (history[block] - history.actual_return).abs().mean()
        inverse[block] = 1 / max(float(error), 1e-6)
    total = sum(inverse.values())
    return {block: value / total for block, value in inverse.items()}


def abstention_reason(analog_n: int, regime_novel: bool, disagreement: float,
                      stale: bool) -> str | None:
    reasons = []
    if analog_n < 5:
        reasons.append("weak_analog_sample")
    if regime_novel:
        reasons.append("regime_novel")
    if disagreement >= 0.80:
        reasons.append("strong_model_disagreement")
    if stale:
        reasons.append("stale_data")
    return ",".join(reasons) or None


def _load_replays(con: Any, trajectory_run: str) -> pd.DataFrame:
    analog = con.execute(
        "SELECT secid,horizon,cutoff,effective_n,forecast_median_return,actual_return,"
        "baseline_return,history_end FROM analog_oos_replays WHERE run_id=? AND horizon IN "
        "(5,20,60,120,250) ORDER BY secid,horizon,cutoff", [trajectory_run]
    ).df()
    existing = con.execute(
        """SELECT secid,horizon,trade_date cutoff,median(predicted_return) pooled
           FROM adaptive_fold_predictions WHERE scope='pooled_loo'
             AND horizon IN (5,20,60,120,250) AND predicted_return IS NOT NULL
           GROUP BY 1,2,3"""
    ).df()
    frame = analog.merge(existing, on=["secid", "horizon", "cutoff"], how="left")
    regimes = con.execute(
        """SELECT trade_date cutoff,regime FROM regime_timeline_v2
           WHERE selected AND run_id=(SELECT run_id FROM regime_intelligence_runs
             WHERE status='completed' ORDER BY finished_at DESC LIMIT 1)"""
    ).df()
    events = con.execute(
        "SELECT DISTINCT trade_date cutoff,TRUE event_active FROM historical_event_timeline WHERE pit_safe"
    ).df()
    frame = frame.merge(regimes, on="cutoff", how="left").merge(events, on="cutoff", how="left")
    frame["event_active"] = frame.event_active.eq(True)
    frame["technical"] = frame.groupby(["secid", "horizon"])["actual_return"].shift(1)
    frame["pooled"] = frame.pooled.fillna(frame.technical)
    frame["analog"] = frame.forecast_median_return
    frame["baseline"] = frame.baseline_return
    return frame


def _oos_rows(frame: pd.DataFrame, run_id: str) -> tuple[list[list], list[list]]:
    predictions: list[list] = []
    evidence: list[list] = []
    for (secid, horizon), group in frame.groupby(["secid", "horizon"], sort=True):
        group = group.sort_values("cutoff").reset_index(drop=True)
        holdout_start = int(len(group) * (1 - HOLDOUT_FRACTION))
        for position, row in group.iterrows():
            available = {name: float(row[name]) for name in ("baseline", "technical", "pooled", "analog")
                         if pd.notna(row[name])}
            if not available:
                continue
            prior = group.iloc[:position]
            weights = performance_weights(prior, list(available))
            equal = float(np.mean(list(available.values())))
            weighted = float(sum(available[name] * weights[name] for name in available))
            regime_prior = prior.loc[prior.regime == row.regime] if pd.notna(row.regime) else prior.iloc[0:0]
            event_prior = prior.loc[prior.event_active == row.event_active]
            regime_value = float(regime_prior.actual_return.tail(100).median()) if len(regime_prior) else 0.0
            event_value = float(event_prior.actual_return.tail(50).median()) if len(event_prior) else 0.0
            variants = {
                "existing": available.get("pooled", available.get("baseline", 0.0)),
                "existing_plus_analog": (available.get("pooled", 0.0) + available.get("analog", 0.0)) / 2,
                "existing_analog_regime": (weighted + regime_value) / 2,
                "existing_analog_regime_events": (weighted + regime_value + event_value) / 3,
                "static_equal_weight": equal,
                "oos_performance_weighted": weighted,
            }
            disagreement = disagreement_score([*available.values(), regime_value, event_value])
            reason = abstention_reason(int(row.effective_n), False, disagreement, False)
            for variant, prediction in variants.items():
                predictions.append([
                    run_id, secid, int(horizon), row.cutoff, variant, prediction,
                    float(row.actual_return), abs(prediction - float(row.actual_return)),
                    bool(np.sign(prediction) == np.sign(row.actual_return)), disagreement,
                    reason is not None, reason, row.history_end, position >= holdout_start,
                    True, False,
                ])
            confidence = min(1.0, int(row.effective_n) / 20)
            for block in BLOCKS:
                value = available.get(block)
                status = "ready" if value is not None else "insufficient_data"
                evidence.append([
                    run_id, secid, int(horizon), row.cutoff, block,
                    int(np.sign(value)) if value is not None else None,
                    abs(value) if value is not None else None, confidence if value is not None else 0.0,
                    int(row.effective_n) if block == "analog" else len(prior),
                    None, None, value, status, block not in available,
                    json.dumps({"visible_block": True, "causal_claim": False}),
                ])
    return predictions, evidence


def _bulk(con: Any, table: str, columns: tuple[str, ...], rows: list[list]) -> None:
    if not rows:
        return
    name = f"_{table}_batch"
    con.register(name, pd.DataFrame.from_records(rows, columns=columns))
    fields = ",".join(columns)
    con.execute(f"INSERT OR REPLACE INTO {table} ({fields}) SELECT {fields} FROM {name}")
    con.unregister(name)


def _write_current(con: Any, run_id: str, event_run: str, cutoff) -> int:
    rows = []
    for secid in INSTRUMENTS:
        event = con.execute(
            "SELECT novelty_status FROM current_event_contexts WHERE run_id=? AND secid=?",
            [event_run, secid],
        ).fetchone()
        for horizon in HORIZONS:
            analog = con.execute(
                """SELECT median(median_return),max(effective_n) FROM analog_terminal_distributions
                   WHERE secid=? AND horizon=? AND status='ready'""", [secid, horizon]
            ).fetchone()
            value, effective_n = analog if analog else (None, 0)
            evidence = {block: {"status": "insufficient_data"} for block in BLOCKS}
            if value is not None:
                evidence["analog"] = {"status": "ready", "value": value, "effective_n": effective_n}
            novel = not event or event[0] == "event_context_novel"
            reason = abstention_reason(int(effective_n or 0), novel, 0.0, False)
            signal = "positive" if value and value > 0 else "negative" if value and value < 0 else "unknown"
            rows.append([run_id, secid, horizon, cutoff, signal, value, "not_measurable",
                         reason is not None, reason, json.dumps(evidence),
                         "shadow" if value is not None else "insufficient_data", True, False])
    _bulk(con, "current_fusion_research",
          ("run_id", "secid", "horizon", "cutoff", "signal", "predicted_return",
           "disagreement", "abstained", "abstention_reason", "evidence_json", "status",
           "shadow_only", "probability_allowed"), rows)
    return len(rows)


def run_predictive_fusion(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        """SELECT e.run_id,e.cutoff,t.run_id FROM event_analog_runs e
           JOIN analog_trajectory_runs t ON t.run_id=e.trajectory_run_id
           WHERE e.status='completed' AND t.status='completed' ORDER BY e.finished_at DESC LIMIT 1"""
    ).fetchone()
    if not source:
        raise ValueError("completed Stage 46 event-conditioned run is required")
    event_run, cutoff, trajectory_run = source
    run_id = hashlib.sha256(f"{event_run}|{cutoff}|{VERSION}".encode()).hexdigest()[:20]
    for table in ("predictive_fusion_runs", "fusion_evidence_blocks", "fusion_oos_predictions",
                  "current_fusion_research"):
        con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
    con.execute(
        "INSERT INTO predictive_fusion_runs (run_id,event_run_id,created_at,status,oos_rows,"
        "current_rows,methodology_version,details_json) VALUES (?,?,current_timestamp,'running',0,0,?,?)",
        [run_id, event_run, VERSION, json.dumps({"shadow_only": True})],
    )
    frame = _load_replays(con, trajectory_run)
    predictions, evidence = _oos_rows(frame, run_id)
    _bulk(con, "fusion_oos_predictions",
          ("run_id", "secid", "horizon", "cutoff", "variant", "predicted_return",
           "actual_return", "absolute_error", "direction_correct", "disagreement", "abstained",
           "abstention_reason", "train_end", "holdout", "shadow_only", "probability_allowed"),
          predictions)
    _bulk(con, "fusion_evidence_blocks",
          ("run_id", "secid", "horizon", "cutoff", "block", "direction", "strength",
           "confidence", "effective_n", "oos_quality", "live_quality", "value", "status",
           "informational_only", "details_json"), evidence)
    current = _write_current(con, run_id, event_run, cutoff)
    con.execute(
        "UPDATE predictive_fusion_runs SET finished_at=current_timestamp,status='completed',"
        "oos_rows=?,current_rows=?,details_json=? WHERE run_id=?",
        [len(predictions), current, json.dumps({"holdout_weights_tuned": False,
         "production_changes": 0, "probability_gate_changed": False, "shadow_only": True}), run_id],
    )
    return {"run_id": run_id, "oos_predictions": len(predictions), "evidence": len(evidence),
            "current": current, "cutoff": cutoff}


def fusion_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,oos_rows,current_rows FROM predictive_fusion_runs "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "oos_predictions", "current"), row, strict=True
    ))
