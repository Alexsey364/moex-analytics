"""Untouched-holdout and block-bootstrap evaluation of stored OOS predictions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score

from .schema import DDL
from .strict_analogs import (
    KS,
    METHODS,
    analog_outcomes,
    feature_frame,
    fit_transform_policy,
    load_prices,
    prediction_record,
)

VERSION = "strict-analog-validation-v2-frozen-policy"
BOOTSTRAP_ITERATIONS = 500


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def validation_metrics(frame: pd.DataFrame) -> dict:
    clean = frame.dropna(subset=["predicted_return", "actual_return"])
    if clean.empty:
        return {"observations": 0}
    actual_sign = (clean.actual_return > 0).astype(int)
    predicted_sign = (clean.predicted_return > 0).astype(int)
    balanced = (
        float(balanced_accuracy_score(actual_sign, predicted_sign))
        if actual_sign.nunique() > 1 else None
    )
    errors = clean.predicted_return - clean.actual_return
    spearman = clean.predicted_return.corr(clean.actual_return, method="spearman")
    coverage_50 = (
        float(((clean.actual_return >= clean.q25) & (clean.actual_return <= clean.q75)).mean())
        if {"q25", "q75"} <= set(clean) else None
    )
    coverage_80 = (
        float(((clean.actual_return >= clean.q10) & (clean.actual_return <= clean.q90)).mean())
        if {"q10", "q90"} <= set(clean) else None
    )
    return {
        "observations": len(clean),
        "balanced_accuracy": balanced,
        "sign_accuracy": float((actual_sign == predicted_sign).mean()),
        "mae": float(errors.abs().mean()),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "spearman": float(spearman) if pd.notna(spearman) else None,
        "rank_ic": float(spearman) if pd.notna(spearman) else None,
        "abstention_rate": float(clean.abstained.mean()),
        "coverage_50": coverage_50,
        "coverage_80": coverage_80,
    }


def block_bootstrap_delta(model_errors: np.ndarray, baseline_errors: np.ndarray,
                          block_length: int, iterations: int = BOOTSTRAP_ITERATIONS,
                          seed: int = 42) -> tuple[float, float, float]:
    """Return baseline MAE minus model MAE using contiguous blocks."""
    difference = np.asarray(baseline_errors) - np.asarray(model_errors)
    difference = difference[np.isfinite(difference)]
    if len(difference) < max(20, block_length * 2):
        return float(np.mean(difference)) if len(difference) else np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(difference) - block_length + 1))
    samples = []
    blocks_needed = int(np.ceil(len(difference) / block_length))
    for _ in range(iterations):
        chosen = rng.choice(starts, blocks_needed, replace=True)
        indices = np.concatenate([np.arange(start, start + block_length) for start in chosen])
        samples.append(float(difference[indices[: len(difference)]].mean()))
    return float(difference.mean()), float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def evidence_status(variant: str, improvement: float, ci_low: float, robust: bool) -> str:
    if not np.isfinite(improvement) or improvement <= 0:
        return "NO_EVIDENCE"
    if not np.isfinite(ci_low) or ci_low <= 0:
        return "WEAK_EVIDENCE"
    if variant == "existing_plus_analog":
        return "ANALOG_USEFUL"
    if "analog" in variant or "weighted" in variant:
        return "SHADOW_CANDIDATE" if robust else "FUSION_IMPROVED"
    return "WEAK_EVIDENCE"


def _load(con: Any, fusion_run: str) -> pd.DataFrame:
    frame = con.execute(
        "SELECT * FROM fusion_oos_predictions_v2 WHERE run_id=? "
        "ORDER BY secid,horizon,cutoff,variant,evaluation_mode",
        [fusion_run],
    ).df()
    snapshots = con.execute(
        "SELECT instrument secid,horizon,train_end policy_train_end,validation_start,validation_end,"
        "holdout_start,holdout_end,policy_hash snapshot_hash FROM fusion_policy_snapshots WHERE run_id=?",
        [fusion_run],
    ).df()
    frame = frame.merge(snapshots, on=["secid", "horizon"], how="inner")
    regimes = con.execute(
        """SELECT trade_date cutoff,regime FROM regime_timeline_v2 WHERE selected AND
           run_id=(SELECT run_id FROM regime_intelligence_runs WHERE status='completed'
                   ORDER BY finished_at DESC LIMIT 1)"""
    ).df()
    events = con.execute(
        "SELECT DISTINCT trade_date cutoff,TRUE event_active FROM historical_event_timeline WHERE pit_safe"
    ).df()
    frame = frame.merge(regimes, on="cutoff", how="left").merge(events, on="cutoff", how="left")
    frame["event_active"] = frame.event_active.eq(True)
    return frame


def _evaluate(frame: pd.DataFrame, run_id: str) -> tuple[list[list], list[list]]:
    scorecards = []
    bootstraps = []
    for (secid, horizon), cell in frame.groupby(["secid", "horizon"], sort=True):
        for split, mode in (("validation", "pseudo_oos_adaptive"),
                            ("holdout", "untouched_holdout_frozen")):
            mode_cell = cell.loc[cell.evaluation_mode == mode]
            if split == "validation":
                mode_cell = mode_cell.loc[
                    (mode_cell.cutoff >= mode_cell.validation_start)
                    & (mode_cell.cutoff <= mode_cell.validation_end)
                ]
            baseline = mode_cell.loc[
                mode_cell.variant == "existing", ["cutoff", "absolute_error"]
            ].rename(columns={"absolute_error": "baseline_error"})
            for variant, variant_frame in mode_cell.groupby("variant", sort=True):
                joined = variant_frame.merge(baseline, on="cutoff", how="inner")
                sample = joined
                metrics = validation_metrics(sample)
                if not metrics.get("observations"):
                    continue
                estimate, low, high = block_bootstrap_delta(
                    sample.absolute_error.to_numpy(), sample.baseline_error.to_numpy(),
                    max(5, int(horizon)), seed=42 + int(horizon),
                )
                robust = metrics["observations"] >= 100 and metrics["abstention_rate"] < 0.5
                status = evidence_status(variant, estimate, low, robust)
                dates = pd.to_datetime(sample.cutoff)
                boundary = sample.iloc[0]
                policy_end = boundary.policy_train_end if split == "validation" else boundary.validation_end
                scorecards.append([
                    run_id, secid, int(horizon), variant, split, "all",
                    metrics["observations"], float(metrics["observations"]),
                    metrics["balanced_accuracy"], metrics["sign_accuracy"], metrics["mae"],
                    metrics["rmse"], metrics["spearman"], metrics["rank_ic"], None, None, None,
                    float(sample.baseline_error.mean()), estimate, status,
                    metrics["abstention_rate"], policy_end, dates.min(), dates.max(),
                ])
                bootstrap_status = "significant" if np.isfinite(low) and low > 0 else "not_significant"
                bootstraps.append([
                    run_id, secid, int(horizon), variant, split, "mae_improvement",
                    estimate, low, high, max(5, int(horizon)), BOOTSTRAP_ITERATIONS,
                    "existing", bootstrap_status,
                ])
                if split != "holdout":
                    continue
                context_masks = [("event", joined.event_active), ("normal", ~joined.event_active)]
                context_masks.extend(
                    (f"regime_{int(regime)}", joined.regime == regime)
                    for regime in sorted(joined.regime.dropna().unique())
                )
                for context, mask in context_masks:
                    sample = joined.loc[mask]
                    metrics = validation_metrics(sample)
                    if not metrics.get("observations"):
                        continue
                    improvement = float(sample.baseline_error.mean() - metrics["mae"])
                    scorecards.append([
                        run_id, secid, int(horizon), variant, "holdout", context,
                        metrics["observations"], float(metrics["observations"]),
                        metrics["balanced_accuracy"], metrics["sign_accuracy"], metrics["mae"],
                        metrics["rmse"], metrics["spearman"], metrics["rank_ic"], None, None, None,
                        float(sample.baseline_error.mean()), improvement,
                        "WEAK_EVIDENCE" if improvement > 0 else "NO_EVIDENCE",
                        metrics["abstention_rate"], boundary.validation_end,
                        pd.to_datetime(sample.cutoff).min(), pd.to_datetime(sample.cutoff).max(),
                    ])
    return scorecards, bootstraps


def _bulk(con: Any, table: str, columns: tuple[str, ...], rows: list[list]) -> None:
    if not rows:
        return
    name = f"_{table}_batch"
    con.register(name, pd.DataFrame.from_records(rows, columns=columns))
    fields = ",".join(columns)
    con.execute(f"INSERT OR REPLACE INTO {table} ({fields}) SELECT {fields} FROM {name}")
    con.unregister(name)


def _event_dates(con: Any) -> set[pd.Timestamp]:
    return {pd.Timestamp(row[0]) for row in con.execute(
        "SELECT DISTINCT trade_date FROM historical_event_timeline WHERE pit_safe"
    ).fetchall()}


def _strict_method_replay(con: Any, run_id: str, fusion_run: str) -> tuple[list[list], list[list]]:
    predictions = []
    selections = []
    events = _event_dates(con)
    snapshots = con.execute(
        "SELECT instrument,horizon,train_end,validation_start,validation_end,holdout_start,"
        "holdout_end,policy_hash FROM fusion_policy_snapshots WHERE run_id=? ORDER BY 1,2",
        [fusion_run],
    ).fetchall()
    for snapshot in snapshots:
        (secid, horizon, train_end, validation_start, validation_end,
         holdout_start, holdout_end, base_hash) = snapshot
        prices = load_prices(con, secid)
        if prices.empty:
            continue
        features = feature_frame(prices)
        train_policy = fit_transform_policy(features, train_end)
        holdout_policy = fit_transform_policy(features, validation_end)
        validation_dates = prices.index[(prices.index >= pd.Timestamp(validation_start))
                                        & (prices.index <= pd.Timestamp(validation_end))][::10]
        holdout_dates = prices.index[(prices.index >= pd.Timestamp(holdout_start))
                                     & (prices.index <= pd.Timestamp(holdout_end))][::10]
        positions = {date: position for position, date in enumerate(prices.index)}
        for method in METHODS:
            validation_by_k: dict[int, list[list]] = {k: [] for k in KS}
            for cutoff in validation_dates:
                position = positions[cutoff]
                if position + horizon >= len(prices):
                    continue
                outcomes = analog_outcomes(
                    prices, features, cutoff, int(horizon), max(KS), method,
                    train_policy, cutoff, events,
                )
                for k in KS:
                    record = prediction_record(outcomes[:k])
                    if not record:
                        continue
                    actual = float(prices.iloc[position + horizon] / prices.iloc[position] - 1)
                    validation_by_k[k].append([
                        run_id, secid, int(horizon), method, k, cutoff, "validation",
                        record["predicted"], actual, record["q10"], record["q25"],
                        record["q75"], record["q90"], record["n"], None, cutoff, False,
                    ])
            errors = {
                k: float(np.mean([abs(row[7] - row[8]) for row in rows]))
                for k, rows in validation_by_k.items() if rows
            }
            if not errors:
                selections.append([run_id, secid, int(horizon), method, None, None, train_end,
                                   validation_end, holdout_start, train_policy["hash"],
                                   holdout_policy["hash"], None, None, False,
                                   "insufficient_data", "no validation analog sample"])
                continue
            selected_k = min(errors, key=lambda k: (errors[k], k))
            method_payload = {
                "base_policy_hash": base_hash, "method": method, "k": selected_k,
                "scaler_hash": holdout_policy["hash"], "library_end": str(validation_end),
                "regime_fit_end": str(validation_end), "similarity_version": "strict-analog-v2",
            }
            digest = hashlib.sha256(
                json.dumps(method_payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            for rows in validation_by_k.values():
                for row in rows:
                    alternative = {**method_payload, "k": row[4], "selection_only": True}
                    row[14] = digest if row[4] == selected_k else hashlib.sha256(
                        json.dumps(alternative, sort_keys=True, default=str).encode()
                    ).hexdigest()
                    predictions.append(row)
            for cutoff in holdout_dates:
                position = positions[cutoff]
                if position + horizon >= len(prices):
                    continue
                outcomes = analog_outcomes(
                    prices, features, cutoff, int(horizon), selected_k, method,
                    holdout_policy, validation_end, events,
                )
                record = prediction_record(outcomes)
                if not record:
                    continue
                actual = float(prices.iloc[position + horizon] / prices.iloc[position] - 1)
                predictions.append([
                    run_id, secid, int(horizon), method, selected_k, cutoff, "holdout",
                    record["predicted"], actual, record["q10"], record["q25"],
                    record["q75"], record["q90"], record["n"], digest, validation_end, False,
                ])
            selections.append([
                run_id, secid, int(horizon), method, selected_k, errors[selected_k], train_end,
                validation_end, holdout_start, train_policy["hash"], holdout_policy["hash"],
                hashlib.sha256(f"{method}|strict-analog-v2".encode()).hexdigest(), digest,
                False, "selected_on_validation", "holdout library and transforms frozen",
            ])
    return predictions, selections


def _method_scorecards(con: Any, run_id: str, fusion_run: str,
                       predictions: list[list]) -> tuple[list[list], list[list]]:
    frame = pd.DataFrame.from_records(predictions, columns=(
        "run_id", "secid", "horizon", "method", "k", "cutoff", "split",
        "predicted_return", "actual_return", "q10", "q25", "q75", "q90", "effective_n",
        "policy_hash", "library_end", "probability_allowed",
    ))
    if frame.empty:
        return [], []
    frame["cutoff"] = pd.to_datetime(frame.cutoff)
    frame["abstained"] = False
    baseline = con.execute(
        "SELECT secid,horizon,cutoff,evaluation_mode,absolute_error FROM fusion_oos_predictions_v2 "
        "WHERE run_id=? AND variant='existing'", [fusion_run]
    ).df()
    baseline["cutoff"] = pd.to_datetime(baseline.cutoff)
    baseline["split"] = baseline.evaluation_mode.map({
        "pseudo_oos_adaptive": "validation", "untouched_holdout_frozen": "holdout"
    })
    frame = frame.merge(
        baseline[["secid", "horizon", "cutoff", "split", "absolute_error"]].rename(
            columns={"absolute_error": "baseline_error"}
        ), on=["secid", "horizon", "cutoff", "split"], how="left",
    )
    scorecards, bootstraps = [], []
    for (secid, horizon, method, k, split), sample in frame.groupby(
        ["secid", "horizon", "method", "k", "split"], sort=True
    ):
        if split == "holdout":
            selected = con.execute(
                "SELECT selected_k FROM analog_method_selection_v2 WHERE run_id=? AND secid=? "
                "AND horizon=? AND method=?", [run_id, str(secid), int(horizon), str(method)]
            ).fetchone()
            if not selected or k != selected[0]:
                continue
        metrics = validation_metrics(sample)
        if not metrics.get("observations"):
            continue
        paired = sample.dropna(subset=["baseline_error"])
        estimate, low, high = block_bootstrap_delta(
            (paired.predicted_return - paired.actual_return).abs().to_numpy(),
            paired.baseline_error.to_numpy(), max(5, int(horizon)), seed=84 + int(horizon),
        )
        status = evidence_status(f"analog_{method}", estimate, low,
                                 metrics["observations"] >= 100)
        scorecards.append([
            run_id, secid, int(horizon), f"analog_{method}_k{k}", split, "all",
            metrics["observations"], float(sample.effective_n.mean()),
            metrics["balanced_accuracy"], metrics["sign_accuracy"], metrics["mae"],
            metrics["rmse"], metrics["spearman"], metrics["rank_ic"],
            metrics["coverage_50"], metrics["coverage_80"], None,
            float(paired.baseline_error.mean()) if len(paired) else None, estimate, status,
            0.0, sample.library_end.max(), pd.to_datetime(sample.cutoff).min(),
            pd.to_datetime(sample.cutoff).max(),
        ])
        bootstraps.append([
            run_id, secid, int(horizon), f"analog_{method}_k{k}", split, "mae_improvement",
            estimate, low, high, max(5, int(horizon)), BOOTSTRAP_ITERATIONS,
            "existing", "significant" if np.isfinite(low) and low > 0 else "not_significant",
        ])
    return scorecards, bootstraps


def run_strict_validation(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    source = con.execute(
        "SELECT run_id FROM predictive_fusion_runs WHERE status='completed' "
        "AND methodology_version='predictive-fusion-v2-frozen-holdout' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not source:
        raise ValueError("completed Stage 47 fusion run is required")
    fusion_run = source[0]
    run_id = hashlib.sha256(f"{fusion_run}|{VERSION}".encode()).hexdigest()[:20]
    partial = con.execute(
        "SELECT status FROM analog_validation_runs WHERE run_id=?", [run_id]
    ).fetchone()
    saved_predictions = con.execute(
        "SELECT count(*) FROM analog_strict_predictions_v2 WHERE run_id=?", [run_id]
    ).fetchone()[0]
    if partial and partial[0] == "completed" and saved_predictions > 0:
        counts = con.execute(
            "SELECT scorecard_rows,bootstrap_rows FROM analog_validation_runs WHERE run_id=?",
            [run_id],
        ).fetchone()
        return {"run_id": run_id, "scorecards": counts[0], "bootstraps": counts[1],
                "replay_predictions": con.execute(
                    "SELECT count(*) FROM fusion_oos_predictions_v2 WHERE run_id=?", [fusion_run]
                ).fetchone()[0], "strict_analog_predictions": saved_predictions,
                "method_selections": con.execute(
                    "SELECT count(*) FROM analog_method_selection_v2 WHERE run_id=?", [run_id]
                ).fetchone()[0], "idempotent_cached": True}
    resuming = bool(partial and partial[0] == "running" and saved_predictions > 0)
    if not resuming:
        for table in ("analog_validation_runs", "analog_validation_scorecards",
                      "analog_validation_bootstrap", "analog_method_validation_status",
                      "analog_method_selection_v2", "analog_strict_predictions_v2"):
            con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
        con.execute(
            "INSERT INTO analog_validation_runs (run_id,fusion_run_id,created_at,status,scorecard_rows,"
            "bootstrap_rows,methodology_version,details_json) "
            "VALUES (?,?,current_timestamp,'running',0,0,?,?)",
            [run_id, fusion_run, VERSION, json.dumps({"holdout_untouched": True})],
        )
    frame = _load(con, fusion_run)
    scorecards, bootstraps = _evaluate(frame, run_id)
    if resuming:
        strict_predictions = con.execute(
            "SELECT run_id,secid,horizon,method,k,cutoff,split,predicted_return,actual_return,"
            "q10,q25,q75,q90,effective_n,policy_hash,library_end,probability_allowed "
            "FROM analog_strict_predictions_v2 WHERE run_id=?", [run_id]
        ).fetchall()
        selections = con.execute(
            "SELECT * FROM analog_method_selection_v2 WHERE run_id=?", [run_id]
        ).fetchall()
    else:
        strict_predictions, selections = _strict_method_replay(con, run_id, fusion_run)
        _bulk(con, "analog_method_selection_v2",
              ("run_id", "secid", "horizon", "method", "selected_k", "validation_mae",
               "train_end", "validation_end", "holdout_start", "scaler_hash", "regime_model_hash",
               "similarity_hash", "policy_hash", "holdout_touched_for_selection", "status", "reason"),
              selections)
        _bulk(con, "analog_strict_predictions_v2",
              ("run_id", "secid", "horizon", "method", "k", "cutoff", "split",
               "predicted_return", "actual_return", "q10", "q25", "q75", "q90", "effective_n",
               "policy_hash", "library_end", "probability_allowed"), strict_predictions)
    method_scores, method_bootstraps = _method_scorecards(
        con, run_id, fusion_run, strict_predictions
    )
    scorecards.extend(method_scores)
    bootstraps.extend(method_bootstraps)
    _bulk(con, "analog_validation_scorecards",
          ("run_id", "secid", "horizon", "variant", "split", "context", "observations",
           "effective_n", "balanced_accuracy", "sign_accuracy", "mae", "rmse", "spearman",
           "rank_ic", "coverage_50", "coverage_80", "coverage_90", "baseline_mae",
           "mae_improvement", "result_status", "abstention_rate", "train_end", "test_start",
           "test_end"), scorecards)
    _bulk(con, "analog_validation_bootstrap",
          ("run_id", "secid", "horizon", "variant", "split", "metric", "estimate", "ci_low",
           "ci_high", "block_length", "iterations", "baseline_variant", "status"), bootstraps)
    method_rows = []
    for method in METHODS:
        for k in KS:
            for horizon in (1, 5, 20, 60, 120, 250):
                available = any(
                    row[3] == method and row[4] == k and row[2] == horizon
                    for row in strict_predictions
                )
                method_rows.append([
                    run_id, method, k, horizon, "evaluated" if available else "not_evaluated",
                    "validation replay evaluated" if available else "insufficient validation sample",
                    False,
                ])
    _bulk(con, "analog_method_validation_status",
          ("run_id", "method", "k", "horizon", "status", "reason",
           "holdout_touched_for_selection"), method_rows)
    con.execute(
        "UPDATE analog_validation_runs SET finished_at=current_timestamp,status='completed',"
        "scorecard_rows=?,bootstrap_rows=?,details_json=? WHERE run_id=?",
        [len(scorecards), len(bootstraps), json.dumps({"shuffle": False, "embargo": True,
         "holdout_tuned": False, "resumed": resuming, "production_changes": 0,
         "probability_gate_changed": False}), run_id],
    )
    return {"run_id": run_id, "scorecards": len(scorecards), "bootstraps": len(bootstraps),
            "replay_predictions": len(frame), "strict_analog_predictions": len(strict_predictions),
            "method_selections": len(selections)}


def validation_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,scorecard_rows,bootstrap_rows FROM analog_validation_runs "
        "WHERE status='completed' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "scorecards", "bootstraps"), row, strict=True
    ))
