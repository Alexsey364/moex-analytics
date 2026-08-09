"""Same-sample individual-stock evidence tournament (Stage 36)."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import balanced_accuracy_score, mean_absolute_error, mean_squared_error

from .issuer_context import ISSUERS
from .schema import DDL

SECIDS = ("SBERP", "LKOH", "MTSS", "TRNFP", "MOEX", "PHOR", "TATNP", "LSNGP", "X5")
HORIZONS = (20, 60, 120, 250)
MARKET = ("return_20", "return_60", "volatility_20", "log_turnover")
SECTOR = ("sector_return_20", "sector_return_60", "relative_strength_20", "relative_strength_60")
FUNDAMENTAL = ("growth_score", "margin_trend", "fcf_trend", "debt_trend", "roe_trend", "payout_trend")
EXPERIMENTS = {
    "market_only": MARKET,
    "market_sector": MARKET + SECTOR,
    "market_fundamentals": MARKET + FUNDAMENTAL,
    "market_sector_fundamentals": MARKET + SECTOR + FUNDAMENTAL,
    "pooled_transfer_issuer_context": MARKET + SECTOR + FUNDAMENTAL,
}


def _issuer(secid: str) -> str:
    for issuer, (secids, _) in ISSUERS.items():
        if secid in secids:
            return issuer
    return secid


def _frame(con, secid: str, horizon: int) -> pd.DataFrame:
    issuer = _issuer(secid)
    return con.execute(
        f"""WITH asset AS (
          SELECT trade_date,close,value,
          close/lag(close,20) OVER(ORDER BY trade_date)-1 return_20,
          close/lag(close,60) OVER(ORDER BY trade_date)-1 return_60,
          stddev_samp(ln(close/lag_close)) OVER(ORDER BY trade_date ROWS BETWEEN 19 PRECEDING
            AND CURRENT ROW) volatility_20,
          ln(nullif(avg(value) OVER(ORDER BY trade_date ROWS BETWEEN 19 PRECEDING
            AND CURRENT ROW),0)) log_turnover,
          lead(close,{horizon}) OVER(ORDER BY trade_date)/close-1 target_value
          FROM (SELECT *,lag(close) OVER(ORDER BY trade_date) lag_close
            FROM moex_equity_eod WHERE secid=? AND close>0
            QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY value DESC NULLS LAST)=1)
        ) SELECT a.*,s.sector_return_20,s.sector_return_60,s.relative_strength_20,
        s.relative_strength_60,f.growth_score,f.margin_trend,f.fcf_trend,f.debt_trend,
        f.roe_trend,f.payout_trend FROM asset a
        LEFT JOIN issuer_sector_context_daily s ON s.trade_date=a.trade_date AND s.secid=?
        LEFT JOIN issuer_derived_fundamental_features f ON f.trade_date=a.trade_date
          AND f.issuer_group=? WHERE a.target_value IS NOT NULL ORDER BY a.trade_date""",
        [secid, secid, issuer],
    ).df().rename(columns={"target_value": "target"})


def _folds(frame: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
    blocks = np.array_split(np.sort(frame.trade_date.unique()), 5)
    result = []
    for index in range(1, 5):
        train_dates = np.concatenate(blocks[:index])
        if len(train_dates) >= 250 and len(blocks[index]) >= 20:
            result.append((frame.trade_date.isin(train_dates).to_numpy(),
                           frame.trade_date.isin(blocks[index]).to_numpy()))
    return result


def _evaluate(frame: pd.DataFrame, features: tuple[str, ...], target_secid: str | None = None) -> dict:
    usable = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["target"]).copy()
    available = [name for name in features if name in usable and usable[name].notna().any()]
    if not available or len(usable) < 300:
        return {"rows": 0, "folds": 0, "status": "NO_EVIDENCE"}
    predictions = []
    fold_deltas = []
    for fold, (train_mask, test_mask) in enumerate(_folds(usable)):
        train, test = usable.loc[train_mask].copy(), usable.loc[test_mask].copy()
        medians = train[available].median().fillna(0)
        x_train = train[available].fillna(medians)
        x_test = test[available].fillna(medians)
        y = (train.target > 0).astype(int)
        if y.nunique() < 2:
            continue
        classifier = LogisticRegression(C=.2, max_iter=500, random_state=36).fit(x_train, y)
        ridge = Ridge(alpha=10).fit(x_train, train.target)
        probability = classifier.predict_proba(x_test)[:, 1]
        predicted_return = ridge.predict(x_test)
        selected = np.ones(len(test), dtype=bool)
        if target_secid is not None:
            selected = test.secid.eq(target_secid).to_numpy()
        if not selected.any():
            continue
        actual = test.target.to_numpy()[selected]
        predicted = probability[selected] >= .5
        baseline = np.full(len(actual), y.mean() >= .5)
        ba = balanced_accuracy_score(actual > 0, predicted)
        base = balanced_accuracy_score(actual > 0, baseline)
        fold_deltas.append(ba - base)
        predictions.append(pd.DataFrame({"actual": actual, "probability": probability[selected],
                                         "predicted_return": predicted_return[selected],
                                         "fold": fold}))
    if not predictions:
        return {"rows": 0, "folds": 0, "status": "NO_EVIDENCE"}
    out = pd.concat(predictions, ignore_index=True)
    ba = balanced_accuracy_score(out.actual > 0, out.probability >= .5)
    base = balanced_accuracy_score(out.actual > 0, np.full(len(out), out.actual.gt(0).mean() >= .5))
    improvement = ba - base
    se = np.std(fold_deltas, ddof=1) / math.sqrt(len(fold_deltas)) if len(fold_deltas) > 1 else math.inf
    low = improvement - 1.96 * se if math.isfinite(se) else None
    high = improvement + 1.96 * se if math.isfinite(se) else None
    correlation = out.actual.corr(out.predicted_return, method="spearman")
    return {"rows": len(out), "folds": out.fold.nunique(), "baseline": base, "ba": ba,
            "mae": mean_absolute_error(out.actual, out.predicted_return),
            "rmse": math.sqrt(mean_squared_error(out.actual, out.predicted_return)),
            "rank_ic": correlation, "spearman": correlation, "improvement": improvement,
            "low": low, "high": high, "wins": sum(x > 0 for x in fold_deltas),
            "fold_stability": float(np.mean(np.array(fold_deltas) > 0)),
            "regime_stability": None, "status": "WEAK_EVIDENCE" if improvement > 0 else "NO_EVIDENCE",
            "features": available}


def _freeze_benchmark(con) -> str:
    summary = {"stage33_run": con.execute(
        "SELECT run_id FROM clean_relearning_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()[0], "context_run": con.execute(
        "SELECT run_id FROM issuer_context_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()[0]}
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:20]
    con.execute("INSERT OR IGNORE INTO issuer_evidence_benchmarks VALUES (?,current_timestamp,?,?,TRUE)",
                [digest, "pre-stage35-production-and-stage33-research", json.dumps(summary)])
    return digest


def run_issuer_evidence_research(con, progress=None) -> dict:
    con.execute(DDL)
    emit = progress or (lambda _: None)
    started = datetime.now(UTC)
    clock = time.perf_counter()
    benchmark = _freeze_benchmark(con)
    run_id = hashlib.sha256(f"stage36:{started.isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        """UPDATE issuer_evidence_runs SET finished_at=current_timestamp,
        status='interrupted_recoverable' WHERE status='running'"""
    )
    con.execute("INSERT INTO issuer_evidence_runs VALUES (?,?,NULL,'running',?,0,0,0,NULL,0,?)",
                [run_id, started, benchmark, json.dumps({"production_frozen": True})])
    frames = {h: {s: _frame(con, s, h) for s in SECIDS} for h in HORIZONS}
    results = shadows = 0
    for horizon in HORIZONS:
        pooled = pd.concat([f.assign(secid=s) for s, f in frames[horizon].items()], ignore_index=True)
        for secid in SECIDS:
            emit(f"{secid} {horizon}")
            baseline_result = None
            for experiment, features in EXPERIMENTS.items():
                result = _evaluate(
                    pooled if experiment.startswith("pooled") else frames[horizon][secid],
                    features, secid if experiment.startswith("pooled") else None,
                )
                status = result["status"]
                used = set(result.get("features", []))
                context_present = (
                    bool(used.intersection(SECTOR))
                    if experiment == "market_sector"
                    else bool(used.intersection(FUNDAMENTAL))
                    if experiment == "market_fundamentals"
                    else bool(used.intersection(SECTOR)) and bool(used.intersection(FUNDAMENTAL))
                    if experiment == "market_sector_fundamentals"
                    else bool(used.intersection(SECTOR))
                )
                if (
                    result.get("low") is not None
                    and result["low"] > 0
                    and experiment != "market_only"
                    and context_present
                    and result["rows"] >= 500
                    and result["folds"] >= 3
                    and result.get("fold_stability", 0) >= 0.75
                ):
                    status = "SHADOW_CANDIDATE"
                    shadows += 1
                values = [result.get(k) for k in ("baseline", "ba", "mae", "rmse", "rank_ic",
                          "spearman", "improvement", "low", "high", "wins", "fold_stability",
                          "regime_stability")]
                con.execute(
                    """INSERT INTO issuer_evidence_results VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,FALSE,?)""",
                    [run_id, secid, horizon, experiment, "pooled_linear", result["rows"],
                     result["folds"], *values, status,
                     json.dumps({"features": result.get("features", []), "live_matured": 0,
                                 "time_oos": True, "same_sample": True})],
                )
                if experiment == "market_only":
                    baseline_result = result
                elif baseline_result and result.get("ba") is not None:
                    family = experiment.removeprefix("market_")
                    con.execute(
                        "INSERT INTO issuer_feature_ablation VALUES (?,?,?,?,?,?,?,?)",
                        [run_id, secid, horizon, family,
                         result["ba"] - baseline_result.get("ba", result["ba"]),
                         result["mae"] - baseline_result.get("mae", result["mae"]), status,
                         json.dumps({"rates_removed": False, "fx_removed": False})],
                    )
                results += 1
            for unavailable_family in ("valuation", "rates", "fx"):
                con.execute(
                    "INSERT INTO issuer_feature_ablation VALUES (?,?,?,?,NULL,NULL,'NO_EVIDENCE',?)",
                    [run_id, secid, horizon, unavailable_family,
                     json.dumps({"reason": "no validated same-sample feature in Stage35"})],
                )
    runtime = time.perf_counter() - clock
    con.execute(
        """UPDATE issuer_evidence_runs SET finished_at=current_timestamp,status='completed',
        results=?,shadow_candidates=?,probability_approved=0,runtime_seconds=?,details_json=?
        WHERE run_id=?""",
        [results, shadows, runtime, json.dumps({"automatic_promotion": False,
         "production_changes": 0, "horizons": HORIZONS, "experiments": list(EXPERIMENTS)}), run_id],
    )
    return {"run_id": run_id, "benchmark_hash": benchmark, "results": results,
            "shadow_candidates": shadows, "probability_approved": 0,
            "runtime_seconds": runtime, "production_changes": 0}


def issuer_evidence_status(con) -> dict:
    con.execute(DDL)
    return {"latest": con.execute(
        "SELECT * FROM issuer_evidence_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone(), "statuses": con.execute(
        """SELECT status,count(*) FROM issuer_evidence_results WHERE run_id=(SELECT run_id
        FROM issuer_evidence_runs ORDER BY started_at DESC LIMIT 1) GROUP BY 1"""
    ).fetchall()}
