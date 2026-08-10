"""Transparent temporal fusion with an immutable untouched-holdout policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from moex_analytics.analog_engine.core import INSTRUMENTS

from .schema import DDL

VERSION = "predictive-fusion-v2-frozen-holdout"
HORIZONS = (5, 20, 60, 120, 250)
BLOCKS = ("baseline", "technical", "pooled", "regime", "sector", "macro", "fundamental",
          "valuation", "analog", "event_analog", "meta_confidence")
MIN_WEIGHT_HISTORY = 100
TRAIN_FRACTION = 0.60
VALIDATION_FRACTION = 0.20
DEFAULT_ABSTENTION_THRESHOLD = 0.80


@dataclass(frozen=True)
class FrozenBoundaries:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp
    holdout_start: pd.Timestamp
    holdout_end: pd.Timestamp


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def frozen_boundaries(dates: pd.Series) -> FrozenBoundaries:
    ordered = pd.Series(pd.to_datetime(dates).drop_duplicates().sort_values().to_numpy())
    if len(ordered) < 10:
        raise ValueError("at least ten dates are required for train/validation/holdout")
    train_stop = max(1, int(len(ordered) * TRAIN_FRACTION))
    validation_stop = max(train_stop + 1, int(len(ordered) * (TRAIN_FRACTION + VALIDATION_FRACTION)))
    validation_stop = min(validation_stop, len(ordered) - 1)
    return FrozenBoundaries(
        ordered.iloc[0], ordered.iloc[train_stop - 1], ordered.iloc[train_stop],
        ordered.iloc[validation_stop - 1], ordered.iloc[validation_stop], ordered.iloc[-1],
    )


def disagreement_score(values: list[float]) -> float:
    clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    if len(clean) < 2:
        return 0.0
    return float(np.std(np.sign(clean)))


def performance_weights(history: pd.DataFrame, blocks: list[str]) -> dict[str, float]:
    """Estimate weights from the caller-provided pre-holdout sample only."""
    if not blocks:
        return {}
    if len(history) < MIN_WEIGHT_HISTORY:
        return {block: 1 / len(blocks) for block in blocks}
    inverse = {}
    for block in blocks:
        error = (history[block] - history.actual_return).abs().mean()
        inverse[block] = 1 / max(float(error), 1e-6)
    total = sum(inverse.values())
    return {block: value / total for block, value in inverse.items()}


def abstention_reason(analog_n: int, regime_novel: bool, disagreement: float,
                      stale: bool, threshold: float = DEFAULT_ABSTENTION_THRESHOLD) -> str | None:
    reasons = []
    if analog_n < 5:
        reasons.append("weak_analog_sample")
    if regime_novel:
        reasons.append("regime_novel")
    if disagreement >= threshold:
        reasons.append("strong_model_disagreement")
    if stale:
        reasons.append("stale_data")
    return ",".join(reasons) or None


def policy_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


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
    frame["pooled"] = frame.pooled.fillna(frame.baseline_return)
    frame["technical"] = frame.pooled
    frame["analog"] = frame.forecast_median_return
    frame["baseline"] = frame.baseline_return
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
    return frame


def _candidate_values(row, weights: dict[str, float], regime_value: float,
                      event_value: float) -> dict[str, float]:
    available = {name: float(getattr(row, name)) for name in ("baseline", "pooled", "analog")
                 if pd.notna(getattr(row, name))}
    weighted = float(sum(available[name] * weights.get(name, 0.0) for name in available))
    if not weights or sum(weights.get(name, 0.0) for name in available) == 0:
        weighted = float(np.mean(list(available.values())))
    return {
        "existing": available.get("pooled", available.get("baseline", 0.0)),
        "existing_plus_analog": (available.get("pooled", 0.0) + available.get("analog", 0.0)) / 2,
        "existing_analog_regime": (weighted + regime_value) / 2,
        "existing_analog_regime_events": (weighted + regime_value + event_value) / 3,
        "static_equal_weight": float(np.mean(list(available.values()))),
        "oos_performance_weighted": weighted,
    }


def _policy_payload(secid: str, horizon: int, boundaries: FrozenBoundaries,
                    weights: dict[str, float], regime_effects: dict, event_effects: dict,
                    threshold: float) -> dict:
    return {
        "instrument": secid, "horizon": horizon, "dataset_version": "canonical-eod-stage45",
        **{name: value.date().isoformat() for name, value in asdict(boundaries).items()},
        "component_models": ["baseline", "pooled", "analog", "regime", "event_analog"],
        "component_versions": {"analog": "analog-trajectories-v1", "pooled": "adaptive-oos"},
        "feature_versions": {"market_state": "regime-intelligence-v2"},
        "weights": weights, "weighting_method": "validation_frozen_inverse_mae",
        "selected_variant": "oos_performance_weighted",
        "abstention_threshold": threshold, "calibration_version": "none_probability_gated",
        "regime_policy": regime_effects, "analog_policy": {"k": 20, "similarity": "state_return20"},
        "event_policy": event_effects, "scaler_version": "stage44-train-only-robust",
        "pca_version": "stage44-train-only-pca",
    }


def build_frozen_policy(group: pd.DataFrame) -> tuple[dict, FrozenBoundaries, str]:
    """Build once from train+validation; no holdout outcome enters the payload."""
    boundaries = frozen_boundaries(group.cutoff)
    pre_holdout = group.loc[pd.to_datetime(group.cutoff) <= boundaries.validation_end].copy()
    components = ["baseline", "pooled", "analog"]
    weights = performance_weights(pre_holdout, components)
    regime_effects = {
        str(key): float(value) for key, value in pre_holdout.groupby("regime").actual_return.median().items()
    }
    event_effects = {
        str(bool(key)).lower(): float(value)
        for key, value in pre_holdout.groupby("event_active").actual_return.median().items()
    }
    validation = pre_holdout.loc[pd.to_datetime(pre_holdout.cutoff) >= boundaries.validation_start]
    disagreements = validation.apply(
        lambda row: disagreement_score([row.baseline, row.pooled, row.analog]), axis=1
    )
    threshold = float(np.clip(disagreements.quantile(0.90), 0.50, 1.0)) if len(validation) else 0.8
    payload = _policy_payload(str(group.secid.iloc[0]), int(group.horizon.iloc[0]), boundaries,
                              weights, regime_effects, event_effects, threshold)
    return payload, boundaries, policy_hash(payload)


def frozen_holdout_predictions(group: pd.DataFrame, payload: dict, boundaries: FrozenBoundaries,
                               digest: str) -> list[list]:
    """Every holdout row uses the exact same immutable payload and digest."""
    holdout = group.loc[pd.to_datetime(group.cutoff) >= boundaries.holdout_start]
    rows = []
    for row in holdout.itertuples():
        regime_value = payload["regime_policy"].get(str(row.regime), 0.0)
        event_value = payload["event_policy"].get(str(bool(row.event_active)).lower(), 0.0)
        variants = _candidate_values(row, payload["weights"], regime_value, event_value)
        disagreement = disagreement_score([row.baseline, row.pooled, row.analog,
                                            regime_value, event_value])
        reason = abstention_reason(int(row.effective_n), False, disagreement, False,
                                   payload["abstention_threshold"])
        for variant, prediction in variants.items():
            rows.append([
                None, row.secid, int(row.horizon), row.cutoff, variant, "untouched_holdout_frozen",
                prediction, float(row.actual_return), abs(prediction - float(row.actual_return)),
                bool(np.sign(prediction) == np.sign(row.actual_return)), disagreement,
                reason is not None, reason, boundaries.validation_end, "holdout", digest, True, False,
            ])
    return rows


def adaptive_predictions(group: pd.DataFrame) -> list[list]:
    """Separate online-like pseudo-OOS mode; adaptation is explicit in its label."""
    rows = []
    ordered = group.sort_values("cutoff").reset_index(drop=True)
    for position, row in ordered.iterrows():
        prior = ordered.iloc[:position]
        weights = performance_weights(prior, ["baseline", "pooled", "analog"])
        regime_prior = prior.loc[prior.regime == row.regime] if pd.notna(row.regime) else prior.iloc[0:0]
        event_prior = prior.loc[prior.event_active == row.event_active]
        regime_value = float(regime_prior.actual_return.median()) if len(regime_prior) else 0.0
        event_value = float(event_prior.actual_return.median()) if len(event_prior) else 0.0
        variants = _candidate_values(row, weights, regime_value, event_value)
        disagreement = disagreement_score([row.baseline, row.pooled, row.analog,
                                            regime_value, event_value])
        reason = abstention_reason(int(row.effective_n), False, disagreement, False)
        adaptive_hash = policy_hash({"mode": "adaptive", "information_end": row.history_end,
                                     "weights": weights})
        for variant, prediction in variants.items():
            rows.append([
                None, row.secid, int(row.horizon), row.cutoff, variant, "pseudo_oos_adaptive",
                prediction, float(row.actual_return), abs(prediction - float(row.actual_return)),
                bool(np.sign(prediction) == np.sign(row.actual_return)), disagreement,
                reason is not None, reason, row.history_end, "adaptive", adaptive_hash, True, False,
            ])
    return rows


def _bulk(con: Any, table: str, columns: tuple[str, ...], rows: list[list]) -> None:
    if not rows:
        return
    name = f"_{table}_batch"
    con.register(name, pd.DataFrame.from_records(rows, columns=columns))
    fields = ",".join(columns)
    con.execute(f"INSERT OR REPLACE INTO {table} ({fields}) SELECT {fields} FROM {name}")
    con.unregister(name)


def _write_snapshot(con: Any, run_id: str, payload: dict, digest: str) -> None:
    con.execute(
        "INSERT INTO fusion_policy_snapshots (run_id,instrument,horizon,dataset_version,train_start,"
        "train_end,validation_start,validation_end,holdout_start,holdout_end,component_models_json,"
        "component_versions_json,feature_versions_json,weights_json,weighting_method,selected_variant,"
        "abstention_threshold,calibration_version,regime_policy_json,analog_policy_json,scaler_version,"
        "pca_version,created_at,policy_hash,immutable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
        "current_timestamp,?,TRUE)",
        [run_id, payload["instrument"], payload["horizon"], payload["dataset_version"],
         payload["train_start"], payload["train_end"], payload["validation_start"],
         payload["validation_end"], payload["holdout_start"], payload["holdout_end"],
         json.dumps(payload["component_models"]), json.dumps(payload["component_versions"]),
         json.dumps(payload["feature_versions"]), json.dumps(payload["weights"]),
         payload["weighting_method"], payload["selected_variant"], payload["abstention_threshold"],
         payload["calibration_version"], json.dumps(payload["regime_policy"]),
         json.dumps(payload["analog_policy"]), payload["scaler_version"], payload["pca_version"], digest],
    )


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
            snapshot = con.execute(
                "SELECT policy_hash FROM fusion_policy_snapshots "
                "WHERE run_id=? AND instrument=? AND horizon=?",
                [run_id, secid, horizon],
            ).fetchone()
            evidence = {block: {"status": "insufficient_data"} for block in BLOCKS}
            if value is not None:
                evidence["analog"] = {"status": "ready", "value": value, "effective_n": effective_n}
            novel = not event or event[0] == "event_context_novel"
            reason = abstention_reason(int(effective_n or 0), novel, 0.0, False)
            signal = "positive" if value and value > 0 else "negative" if value and value < 0 else "unknown"
            evidence["fusion_policy"] = {"mode": "live_shadow", "hash": snapshot[0] if snapshot else None}
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
    for table in ("predictive_fusion_runs", "fusion_evidence_blocks", "current_fusion_research",
                  "fusion_policy_snapshots", "fusion_oos_predictions_v2"):
        con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
    con.execute(
        "INSERT INTO predictive_fusion_runs (run_id,event_run_id,created_at,status,oos_rows,"
        "current_rows,methodology_version,details_json) VALUES (?,?,current_timestamp,'running',0,0,?,?)",
        [run_id, event_run, VERSION, json.dumps({"holdout_policy": "frozen_before_holdout"})],
    )
    frame = _load_replays(con, trajectory_run)
    predictions = []
    evidence = []
    snapshot_count = 0
    for (secid, horizon), group in frame.groupby(["secid", "horizon"], sort=True):
        payload, boundaries, digest = build_frozen_policy(group)
        _write_snapshot(con, run_id, payload, digest)
        snapshot_count += 1
        frozen = frozen_holdout_predictions(group, payload, boundaries, digest)
        adaptive = adaptive_predictions(group)
        for row in [*frozen, *adaptive]:
            row[0] = run_id
        predictions.extend(frozen)
        predictions.extend(adaptive)
        seen_dates = set()
        for row in frozen:
            if row[3] in seen_dates:
                continue
            seen_dates.add(row[3])
            for block in BLOCKS:
                value = getattr(group.loc[group.cutoff == row[3]].iloc[0], block, None)
                evidence.append([
                    run_id, secid, int(horizon), row[3], block,
                    int(np.sign(value)) if value is not None and pd.notna(value) else None,
                    abs(float(value)) if value is not None and pd.notna(value) else None,
                    min(1.0, float(group.loc[group.cutoff == row[3]].effective_n.iloc[0]) / 20)
                    if block == "analog" else 0.0,
                    int(group.loc[group.cutoff == row[3]].effective_n.iloc[0]) if block == "analog" else 0,
                    None, None, float(value) if value is not None and pd.notna(value) else None,
                    "ready" if value is not None and pd.notna(value) else "insufficient_data",
                    block not in {"baseline", "pooled", "analog"},
                    json.dumps({"policy_hash": digest, "evaluation_mode": "untouched_holdout_frozen"}),
                ])
    _bulk(con, "fusion_oos_predictions_v2",
          ("run_id", "secid", "horizon", "cutoff", "variant", "evaluation_mode",
           "predicted_return", "actual_return", "absolute_error", "direction_correct", "disagreement",
           "abstained", "abstention_reason", "information_end", "split", "policy_hash",
           "shadow_only", "probability_allowed"), predictions)
    _bulk(con, "fusion_evidence_blocks",
          ("run_id", "secid", "horizon", "cutoff", "block", "direction", "strength",
           "confidence", "effective_n", "oos_quality", "live_quality", "value", "status",
           "informational_only", "details_json"), evidence)
    current = _write_current(con, run_id, event_run, cutoff)
    con.execute(
        "UPDATE predictive_fusion_runs SET finished_at=current_timestamp,status='completed',"
        "oos_rows=?,current_rows=?,details_json=? WHERE run_id=?",
        [len(predictions), current, json.dumps({"policy_snapshots": snapshot_count,
         "evaluation_modes": ["pseudo_oos_adaptive", "untouched_holdout_frozen", "live_shadow"],
         "holdout_weights_updated": False, "holdout_threshold_updated": False,
         "holdout_calibration_refit": False, "holdout_model_selection": False,
         "production_changes": 0, "probability_gate_changed": False}), run_id],
    )
    return {"run_id": run_id, "oos_predictions": len(predictions), "evidence": len(evidence),
            "snapshots": snapshot_count, "current": current, "cutoff": cutoff}


def fusion_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,oos_rows,current_rows FROM predictive_fusion_runs "
        "WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "oos_predictions", "current"), row, strict=True
    ))
