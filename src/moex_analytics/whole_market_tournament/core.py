"""Stage 77: comparable whole-market evidence tournament."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np

from .schema import ensure_schema

VERSION = "stage80.5-v3"
SEED = 77


def paired_evidence(deltas: np.ndarray, repetitions: int = 1000) -> tuple[float, float, float]:
    """Return bootstrap CI and two-sided sign-permutation p-value for paired gains."""
    clean = np.asarray(deltas, dtype=float)
    clean = clean[np.isfinite(clean)]
    if len(clean) < 20:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(clean), size=(repetitions, len(clean)))
    means = clean[indices].mean(axis=1)
    signs = rng.choice((-1.0, 1.0), size=(repetitions, len(clean)))
    permuted = (clean * signs).mean(axis=1)
    p_value = (np.count_nonzero(np.abs(permuted) >= abs(clean.mean())) + 1) / (repetitions + 1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)), float(p_value)


def bh_adjust(values: list[float | None]) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    valid = [(index, value) for index, value in enumerate(values) if value is not None and np.isfinite(value)]
    ordered = sorted(valid, key=lambda item: item[1])
    running = 1.0
    for reverse_rank, (index, value) in enumerate(reversed(ordered), start=1):
        rank = len(ordered) - reverse_rank + 1
        running = min(running, float(value) * len(ordered) / rank)
        result[index] = running
    return result


def _latest(con: duckdb.DuckDBPyConnection, table: str) -> str:
    row = con.execute(f"SELECT run_id FROM {table} ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError(f"required source is missing: {table}")
    return row[0]


def run_whole_market_tournament(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ensure_schema(con)
    sources = {
        "market": _latest(con, "market_forecast_runs"),
        "sector": _latest(con, "sector_rotation_runs"),
        "stock": _latest(con, "conditioned_stock_runs"),
        "fusion": _latest(con, "market_analog_fusion_runs"),
        "state": _latest(con, "whole_market_state_runs"),
    }
    run_id = hashlib.sha256(f"{VERSION}|{json.dumps(sources, sort_keys=True)}".encode()).hexdigest()[:20]
    if con.execute("SELECT 1 FROM whole_market_tournament_runs WHERE run_id=?", [run_id]).fetchone():
        return _status(con, run_id) | {"idempotent": True}
    entries: list[dict[str, Any]] = []
    market = con.execute(
        """SELECT horizon,model,observations,balanced_accuracy,baseline_balanced_accuracy,
        improvement_vs_baseline FROM market_forecast_scorecards
        WHERE run_id=? AND sample='frozen_holdout'""",
        [sources["market"]],
    ).fetchall()
    for horizon, model, n, score, baseline, gain in market:
        entries.append(
            dict(
                scope="market",
                instrument="IMOEX",
                horizon=horizon,
                variant=model,
                metric="balanced_accuracy",
                observations=n,
                score=score,
                baseline=baseline,
                gain=gain,
                ci_low=None,
                ci_high=None,
                p=None,
                subperiod=None,
                regime=None,
                details={"paired_trail": "not_persisted_in_stage72"},
            )
        )
    sector = con.execute(
        """SELECT horizon,observations,rank_ic,baseline_rank_ic FROM sector_rotation_scorecards
        WHERE run_id=? AND sample='frozen_holdout'""",
        [sources["sector"]],
    ).fetchall()
    for horizon, n, score, baseline in sector:
        entries.append(
            dict(
                scope="sector",
                instrument="ALL_SECTORS",
                horizon=horizon,
                variant="momentum_rank",
                metric="rank_ic",
                observations=n,
                score=score,
                baseline=baseline,
                gain=score - baseline,
                ci_low=None,
                ci_high=None,
                p=None,
                subperiod=None,
                regime=None,
                details={"cross_sectional": True},
            )
        )
    stock = con.execute(
        """SELECT secid,horizon,feature_block,observations,model_mae,baseline_mae,improvement
        FROM conditioned_stock_scorecards WHERE run_id=?""",
        [sources["stock"]],
    ).fetchall()
    for secid, horizon, block, n, score, baseline, gain in stock:
        entries.append(
            dict(
                scope="stock",
                instrument=secid,
                horizon=horizon,
                variant=block,
                metric="negative_mae",
                observations=n,
                score=-score,
                baseline=-baseline,
                gain=gain,
                ci_low=None,
                ci_high=None,
                p=None,
                subperiod=None,
                regime=None,
                details={"ablation_family": block},
            )
        )
    fusion_cards = con.execute(
        """SELECT secid,horizon,observations,fused_mae,analog_mae,improvement
        FROM market_analog_fusion_scorecards WHERE run_id=?""",
        [sources["fusion"]],
    ).fetchall()
    for secid, horizon, n, fused_mae, analog_mae, gain in fusion_cards:
        raw = con.execute(
            """SELECT f.cutoff,f.analog_error-f.fused_error delta,s.market_state_label
            FROM market_analog_fusion_oos f LEFT JOIN whole_market_state_daily s
            ON s.run_id=? AND s.trade_date=f.cutoff
            WHERE f.run_id=? AND f.secid=? AND f.horizon=? ORDER BY f.cutoff""",
            [sources["state"], sources["fusion"], secid, horizon],
        ).fetchall()
        deltas = np.asarray([row[1] for row in raw], dtype=float)
        low, high, p_value = paired_evidence(deltas)
        midpoint = len(deltas) // 2
        subperiod = bool(midpoint and np.mean(deltas[:midpoint]) * np.mean(deltas[midpoint:]) > 0)
        regime_groups: dict[str, list[float]] = {}
        for _, delta, regime in raw:
            if regime is not None:
                regime_groups.setdefault(regime, []).append(float(delta))
        regime_means = {
            regime: float(np.mean(values)) for regime, values in regime_groups.items() if len(values) >= 20
        }
        regime_stable = len(regime_means) >= 2 and all(value > 0 for value in regime_means.values())
        entries.append(
            dict(
                scope="fusion",
                instrument=secid,
                horizon=horizon,
                variant="market_sector_issuer_analog",
                metric="negative_mae",
                observations=n,
                score=-fused_mae,
                baseline=-analog_mae,
                gain=gain,
                ci_low=low,
                ci_high=high,
                p=p_value,
                subperiod=subperiod,
                regime=regime_stable,
                details={
                    "expanding_train_only": True,
                    "event_context": "informational",
                    "regime_means": regime_means,
                },
            )
        )
    adjusted = bh_adjust([item["p"] for item in entries])
    rows = []
    for item, adjusted_p in zip(entries, adjusted, strict=True):
        proven = (
            item["ci_low"] is not None
            and item["ci_low"] > 0
            and adjusted_p is not None
            and adjusted_p < 0.05
            and item["regime"] is True
        )
        # A positive aggregate, bootstrap, BH, subperiod and regime result is not
        # sufficient for production candidacy without a separately persisted
        # walk-forward fold-stability gate and independent replication.
        status = "shadow_candidate" if proven and item["subperiod"] is True else "experimental"
        if item["gain"] is None or item["gain"] <= 0:
            status = "rejected"
        if item["p"] is None and status != "rejected":
            status = "insufficient_evidence"
        rows.append(
            [
                run_id,
                item["scope"],
                item["instrument"],
                item["horizon"],
                item["variant"],
                item["metric"],
                item["observations"],
                item["score"],
                item["baseline"],
                item["gain"],
                item["ci_low"],
                item["ci_high"],
                item["p"],
                adjusted_p,
                item["subperiod"],
                item["regime"],
                bool(item["p"] is not None and item["p"] < 0.05),
                status,
                json.dumps(item["details"]),
            ]
        )
    con.executemany(
        """INSERT INTO whole_market_tournament_entries
        (run_id,scope,instrument,horizon,variant,metric,observations,score,baseline_score,improvement,
        ci_low,ci_high,p_value,adjusted_p_value,subperiod_stable,regime_stable,permutation_passed,status,
        details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    cutoff = con.execute(
        "SELECT max(cutoff) FROM market_analog_fusion_oos WHERE run_id=?", [sources["fusion"]]
    ).fetchone()[0]
    con.execute(
        """INSERT INTO whole_market_tournament_runs
        (run_id,created_at,cutoff,entries,instruments,horizons_json,methodology_version,immutable,
        production_unchanged,probability_gate_unchanged,status,details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            run_id,
            datetime.now(UTC),
            cutoff,
            len(rows),
            len({r[2] for r in rows}),
            json.dumps([1, 5, 20, 60, 120, 250]),
            VERSION,
            True,
            True,
            True,
            "completed",
            json.dumps(
                {
                    "bh_multiple_testing": True,
                    "auto_promotion": False,
                    "production_candidate_requires_fold_stability": True,
                    "independent_replication": False,
                }
            ),
        ],
    )
    return _status(con, run_id) | {"idempotent": False}


def _status(con: duckdb.DuckDBPyConnection, run_id: str) -> dict[str, Any]:
    row = con.execute(
        """SELECT count(*),sum(status='shadow_candidate'),sum(status='experimental'),
    sum(status='insufficient_evidence'),sum(status='rejected') FROM whole_market_tournament_entries
    WHERE run_id=?""",
        [run_id],
    ).fetchone()
    return dict(
        zip(("entries", "shadow_candidates", "experimental", "insufficient", "rejected"), row, strict=True)
    ) | {"run_id": run_id, "status": "completed", "auto_promoted": False, "probability_gate_changed": False}
