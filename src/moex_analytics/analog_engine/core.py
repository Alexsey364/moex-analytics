"""Leakage-safe market, issuer and portfolio historical analog search."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from sklearn.decomposition import PCA

from .schema import DDL

VERSION = "analog-search-v3"
CONTEXT_STATUSES = {
    "ready",
    "insufficient_context",
    "insufficient_history",
    "insufficient_feature_coverage",
    "insufficient_independent_episodes",
    "method_unavailable",
    "numerical_failure",
    "skipped_quality_gate",
}
MIN_HISTORY = 500
MIN_COVERAGE = 0.60
REQUESTED_K = 50
MIN_MAHALANOBIS_ROWS = 50
MAHALANOBIS_ROWS_PER_FEATURE = 5
MIN_PCA_ROWS = 20
MIN_DTW_PREHISTORY_MULTIPLIER = 2
INSTRUMENTS = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
MARKET_FEATURES = (
    "ret_5",
    "ret_20",
    "ret_60",
    "volatility_5",
    "volatility_20",
    "volatility_60",
    "drawdown_60",
    "breadth_balance",
    "dispersion",
    "turnover_log",
    "rvi_change",
    "rusfar_change",
    "rgbi_change",
    "cny_change",
    "usd_change",
)
ISSUER_FEATURES = (
    "ret_5",
    "ret_20",
    "ret_60",
    "volatility_20",
    "drawdown_60",
    "turnover_log",
    "relative_20",
    "breadth_balance",
    "market_stress",
    "data_coverage",
)


def ensure_schema(con: Any) -> None:
    con.execute(DDL)
    migrations = (
        "ALTER TABLE analog_contexts_v3 ADD COLUMN IF NOT EXISTS status VARCHAR",
        "ALTER TABLE analog_contexts_v3 ADD COLUMN IF NOT EXISTS reason VARCHAR",
        "ALTER TABLE analog_contexts_v3 ADD COLUMN IF NOT EXISTS eligible_rows BIGINT",
        "ALTER TABLE analog_contexts_v3 ADD COLUMN IF NOT EXISTS required_coverage DOUBLE",
        "ALTER TABLE analog_method_diagnostics_v3 ADD COLUMN IF NOT EXISTS requested_k INTEGER",
        "ALTER TABLE analog_method_diagnostics_v3 ADD COLUMN IF NOT EXISTS effective_k INTEGER",
        "ALTER TABLE analog_method_diagnostics_v3 ADD COLUMN IF NOT EXISTS condition_number DOUBLE",
        "ALTER TABLE analog_method_diagnostics_v3 ADD COLUMN IF NOT EXISTS reason VARCHAR",
    )
    for statement in migrations:
        con.execute(statement)


def robust_scale(history: pd.DataFrame, current: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    median = history.median()
    scale = (history.quantile(0.75) - history.quantile(0.25)).replace(0, 1).fillna(1)
    train = ((history.fillna(median) - median) / scale).clip(-12, 12).to_numpy(float)
    point = ((current.fillna(median) - median) / scale).clip(-12, 12).to_numpy(float)
    return train, point


def state_distances(history: pd.DataFrame, current: pd.Series, method: str) -> pd.Series:
    if history.empty or current.empty:
        return pd.Series(dtype=float)
    train, point = robust_scale(history, current)
    if method == "pca":
        if len(train) < MIN_PCA_ROWS:
            raise ValueError("insufficient rows for PCA policy")
        model = PCA(n_components=min(5, train.shape[1], train.shape[0] - 1), random_state=42)
        transformed = model.fit_transform(train)
        target = model.transform(point.reshape(1, -1))[0]
        values = np.sqrt(np.mean((transformed - target) ** 2, axis=1))
    elif method == "mahalanobis":
        if len(train) < max(MIN_MAHALANOBIS_ROWS, train.shape[1] * MAHALANOBIS_ROWS_PER_FEATURE):
            raise ValueError("insufficient rows for stable covariance")
        delta = train - point
        covariance = LedoitWolf().fit(train)
        condition = np.linalg.cond(covariance.covariance_)
        if not np.isfinite(condition) or condition > 1e12:
            raise np.linalg.LinAlgError("unstable regularized covariance")
        precision = covariance.precision_
        values = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", delta, precision, delta), 0))
    elif method == "cosine":
        denominator = np.linalg.norm(train, axis=1) * max(np.linalg.norm(point), 1e-12)
        if np.linalg.norm(point) <= 1e-12 or np.any(np.linalg.norm(train, axis=1) <= 1e-12):
            raise ValueError("zero norm makes cosine unavailable")
        values = 1 - (train @ point) / np.maximum(denominator, 1e-12)
    else:
        values = np.sqrt(np.mean((train - point) ** 2, axis=1))
    return pd.Series(values, index=history.index)


def dtw_distance(left: np.ndarray, right: np.ndarray) -> float:
    """Classic DTW over pre-cutoff paths only."""
    rows, cols = len(left), len(right)
    cost = np.full((rows + 1, cols + 1), np.inf)
    cost[0, 0] = 0
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            cost[i, j] = abs(left[i - 1] - right[j - 1]) + min(
                cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1]
            )
    return float(cost[rows, cols] / max(rows, cols))


def path_distances(series: pd.Series, cutoff_pos: int, window: int, method: str) -> pd.Series:
    if cutoff_pos < window * MIN_DTW_PREHISTORY_MULTIPLIER or len(series) <= cutoff_pos:
        return pd.Series(dtype=float)
    returns = series.pct_change(fill_method=None).fillna(0).to_numpy(float)
    current = returns[cutoff_pos - window + 1 : cutoff_pos + 1]
    result = {}
    for position in range(window - 1, cutoff_pos - window):
        candidate = returns[position - window + 1 : position + 1]
        if method == "dtw":
            result[series.index[position]] = dtw_distance(candidate, current)
        else:
            denom = max(np.linalg.norm(candidate) * np.linalg.norm(current), 1e-12)
            result[series.index[position]] = float(1 - (candidate @ current) / denom)
    return pd.Series(result, dtype=float)


def independent_nearest(distances: pd.Series, separation: int = 20, limit: int = 50) -> pd.Series:
    distances = distances.replace([np.inf, -np.inf], np.nan).dropna()
    if distances.empty:
        return distances
    selected = []
    positions = {date: i for i, date in enumerate(distances.sort_index().index)}
    ranked = distances.rename("distance").to_frame()
    ranked["date_tiebreak"] = ranked.index
    ranked = ranked.sort_values(["distance", "date_tiebreak"], kind="mergesort")
    for date in ranked.index:
        if all(abs(positions[date] - positions[prior]) >= separation for prior in selected):
            selected.append(date)
        if len(selected) >= limit:
            break
    return distances.loc[selected]


def filter_eligible_dates(frame: pd.DataFrame, allowed_dates: set[pd.Timestamp]) -> pd.DataFrame:
    """Optional regime/event filter; an empty match is a valid empty result."""
    if frame.empty or not allowed_dates:
        return frame.iloc[0:0]
    normalized = {pd.Timestamp(value) for value in allowed_dates}
    return frame.loc[frame.index.intersection(normalized)]


def _decomposition(history: pd.DataFrame, current: pd.Series, date) -> tuple[dict, dict]:
    train, point = robust_scale(history, current)
    row = train[history.index.get_loc(date)]
    gaps = np.abs(row - point)
    order = np.argsort(gaps)
    similar = {history.columns[i]: float(gaps[i]) for i in order[: min(5, len(order))]}
    different = {history.columns[i]: float(gaps[i]) for i in order[-min(5, len(order)) :][::-1]}
    return similar, different


def context_policy(frame: pd.DataFrame, core: list[str], optional: list[str]) -> dict[str, Any]:
    available_core = [name for name in core if name in frame and frame[name].notna().sum() > 0]
    if not available_core:
        return {"status": "insufficient_context", "reason": "all required features missing"}
    if frame.empty:
        return {"status": "insufficient_context", "reason": "context frame is empty"}
    current_coverage = float(frame.iloc[-1][available_core].notna().mean())
    if current_coverage < MIN_COVERAGE:
        return {
            "status": "insufficient_feature_coverage",
            "reason": "current core coverage below frozen threshold",
            "coverage": current_coverage,
        }
    features = available_core + [
        name for name in optional if name in frame and frame[name].notna().sum() >= MIN_HISTORY
    ]
    eligible = frame[features].dropna(thresh=max(1, int(np.ceil(len(available_core) * MIN_COVERAGE))))
    if eligible.empty:
        return {
            "status": "insufficient_feature_coverage",
            "reason": "coverage filter removed every row",
            "coverage": current_coverage,
        }
    if len(eligible) < MIN_HISTORY + 250:
        return {
            "status": "insufficient_history",
            "reason": "fewer than frozen minimum historical rows",
            "coverage": current_coverage,
            "eligible": eligible,
            "features": features,
        }
    return {
        "status": "ready",
        "reason": "context passed frozen quality policy",
        "coverage": current_coverage,
        "eligible": eligible,
        "features": features,
    }


def method_distances(
    history: pd.DataFrame, current: pd.Series, method: str
) -> tuple[pd.Series, str, float | None, str]:
    try:
        if method == "mahalanobis":
            train, _ = robust_scale(history, current)
            condition = float(np.linalg.cond(LedoitWolf().fit(train).covariance_))
        else:
            condition = None
        distances = state_distances(history, current, method)
        if distances.empty:
            return distances, "method_unavailable", condition, "method returned no eligible distances"
        return distances, "ready", condition, "eligible"
    except (ValueError, np.linalg.LinAlgError) as exc:
        status = "numerical_failure" if isinstance(exc, np.linalg.LinAlgError) else "method_unavailable"
        return pd.Series(dtype=float), status, None, str(exc)


def _selected_regime(con, state_run):
    rows = con.execute(
        "SELECT trade_date,regime FROM regime_timeline_v2 WHERE run_id=? AND selected ORDER BY trade_date",
        [state_run],
    ).df()
    return dict(zip(pd.to_datetime(rows.trade_date), rows.regime, strict=False))


def _event_dates(con):
    rows = con.execute("SELECT DISTINCT trade_date FROM historical_event_timeline WHERE pit_safe").fetchall()
    return {pd.Timestamp(row[0]) for row in rows}


def _contexts(con, state_run):
    market = (
        con.execute(
            "SELECT * EXCLUDE(run_id) FROM regime_market_state_vectors WHERE run_id=? ORDER BY trade_date",
            [state_run],
        )
        .df()
        .set_index("trade_date")
    )
    market.index = pd.to_datetime(market.index)
    issuers = {}
    for secid in INSTRUMENTS:
        frame = (
            con.execute(
                "SELECT * EXCLUDE(run_id,secid) FROM regime_issuer_state_vectors "
                "WHERE run_id=? AND secid=? ORDER BY trade_date",
                [state_run, secid],
            )
            .df()
            .set_index("trade_date")
        )
        frame.index = pd.to_datetime(frame.index)
        issuers[secid] = frame
    portfolio = pd.concat([frame.add_prefix(f"{secid}_") for secid, frame in issuers.items()], axis=1)
    keep = [
        column
        for column in portfolio
        if column.endswith(("ret_20", "volatility_20", "drawdown_60", "relative_20"))
    ]
    return (
        [("market", "MARKET", market, list(MARKET_FEATURES))]
        + [("issuer", s, f, list(ISSUER_FEATURES)) for s, f in issuers.items()]
        + [("portfolio", "PORTFOLIO", portfolio, keep)]
    )


def _run_analog_search_impl(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    state = con.execute(
        "SELECT run_id,cutoff FROM regime_intelligence_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not state:
        raise ValueError("Stage 43 completed state is required")
    state_run, cutoff = state
    run_id = hashlib.sha256(f"{state_run}|{cutoff}|{VERSION}".encode()).hexdigest()[:20]
    for table in (
        "analog_search_runs_v3",
        "analog_contexts_v3",
        "historical_analogs_v3",
        "analog_method_diagnostics_v3",
    ):
        con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
    con.execute(
        "INSERT INTO analog_search_runs_v3 "
        "(run_id,created_at,status,cutoff,state_run_id,contexts,methods_json,analogs,"
        "methodology_version,details_json) VALUES (?,current_timestamp,'running',?,?,0,'[]',0,?,?)",
        [run_id, cutoff, state_run, VERSION, json.dumps({"resumable": True})],
    )
    regimes = _selected_regime(con, state_run)
    event_dates = _event_dates(con)
    total = 0
    contexts = 0
    methods = (
        ("robust_euclidean", 0),
        ("mahalanobis", 0),
        ("cosine", 0),
        ("pca", 0),
        ("path_cosine", 20),
        ("path_cosine", 60),
        ("path_cosine", 120),
        ("dtw", 20),
    )
    for analog_type, secid, frame, feature_names in _contexts(con, state_run):
        core = (
            ["ret_20", "volatility_20", "breadth_balance"]
            if analog_type == "market"
            else ["ret_20", "volatility_20"]
            if analog_type == "issuer"
            else [name for name in feature_names if name.endswith("ret_20")][:3]
        )
        policy = context_policy(frame, core, [name for name in feature_names if name not in core])
        usable = policy.get("eligible", pd.DataFrame())
        features = policy.get("features", [])
        quality = float(policy.get("coverage", 0.0))
        current_regime = regimes.get(frame.index[-1]) if not frame.empty else None
        contexts += 1
        con.execute(
            "INSERT OR REPLACE INTO analog_contexts_v3 "
            "(run_id,analog_type,secid,cutoff,feature_count,history_rows,current_regime,"
            "current_novelty,data_quality,status,reason,eligible_rows,required_coverage) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run_id,
                analog_type,
                secid,
                cutoff,
                len(features),
                max(0, len(usable) - 250),
                current_regime,
                str(frame.iloc[-1].get("novelty_status", "unknown")) if not frame.empty else "unknown",
                quality,
                policy["status"],
                policy["reason"],
                len(usable),
                MIN_COVERAGE,
            ],
        )
        if policy["status"] != "ready":
            continue
        current = usable.iloc[-1]
        history = usable.iloc[:-250]
        current_regime = regimes.get(usable.index[-1])
        for method, window in methods:
            if window:
                if analog_type == "portfolio" or len(usable) < window * 2 + 250:
                    distances = pd.Series(dtype=float)
                    method_status, condition, reason = (
                        "method_unavailable",
                        None,
                        "path history unavailable for context",
                    )
                else:
                    price_column = "ret_20" if "ret_20" in usable else features[0]
                    distances = path_distances(
                        usable[price_column].cumsum(),
                        len(usable) - 1,
                        window,
                        "dtw" if method == "dtw" else "cosine",
                    )
                    distances = distances[distances.index.isin(history.index)]
                    method_status, condition, reason = (
                        ("ready", None, "eligible")
                        if not distances.empty
                        else ("method_unavailable", None, "short pre-cutoff path history")
                    )
            else:
                distances, method_status, condition, reason = method_distances(history, current, method)
            nearest = independent_nearest(distances, max(20, window), REQUESTED_K)
            ordered = np.sort(distances.to_numpy())
            for rank, (date, distance) in enumerate(nearest.items(), 1):
                percentile = float(np.searchsorted(ordered, distance, side="right") / len(ordered))
                similarity = max(0, 1 - percentile)
                similar, different = _decomposition(history, current, date)
                con.execute(
                    "INSERT OR REPLACE INTO historical_analogs_v3 "
                    "(run_id,analog_type,secid,method,path_window,cutoff,analog_date,"
                    "episode_rank,distance,distance_percentile,similarity_score,feature_coverage,"
                    "regime_agreement,sector_agreement,event_state_agreement,data_quality,"
                    "independent,why_similar_json,why_different_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,TRUE,?,?)",
                    [
                        run_id,
                        analog_type,
                        secid,
                        method,
                        window,
                        cutoff,
                        date,
                        rank,
                        float(distance),
                        percentile,
                        similarity,
                        float(history.loc[date].notna().mean()),
                        regimes.get(date) == current_regime,
                        None,
                        (date in event_dates) == (usable.index[-1] in event_dates),
                        quality,
                        json.dumps(similar),
                        json.dumps(different),
                    ],
                )
                total += 1
            if method_status != "ready":
                status = method_status
            elif len(nearest) < 20:
                status = "insufficient_independent_episodes"
                reason = "episode separation leaves fewer than requested minimum"
            else:
                status = "ready"
            con.execute(
                "INSERT OR REPLACE INTO analog_method_diagnostics_v3 "
                "(run_id,analog_type,secid,method,path_window,candidates,independent_selected,"
                "median_distance,effective_n,status,train_only,requested_k,effective_k,"
                "condition_number,reason) VALUES (?,?,?,?,?,?,?,?,?,?,TRUE,?,?,?,?)",
                [
                    run_id,
                    analog_type,
                    secid,
                    method,
                    window,
                    len(distances),
                    len(nearest),
                    float(nearest.median()) if len(nearest) else None,
                    len(nearest),
                    status,
                    REQUESTED_K,
                    len(nearest),
                    condition,
                    reason,
                ],
            )
    details = {
        "future_data_used": False,
        "learned_similarity": "deferred_until_strict_validation",
        "production_changes": 0,
    }
    ready = con.execute(
        "SELECT count(*) FROM analog_contexts_v3 WHERE run_id=? AND status='ready'", [run_id]
    ).fetchone()[0]
    run_status = "completed" if total else "completed_insufficient_data"
    con.execute(
        "UPDATE analog_search_runs_v3 SET finished_at=current_timestamp,status=?,contexts=?,"
        "methods_json=?,analogs=?,details_json=? WHERE run_id=?",
        [
            run_status,
            contexts,
            json.dumps(methods),
            total,
            json.dumps({**details, "ready_contexts": ready}),
            run_id,
        ],
    )
    return {"run_id": run_id, "contexts": contexts, "analogs": total, "cutoff": cutoff}


def run_analog_search(con: Any) -> dict[str, Any]:
    try:
        return _run_analog_search_impl(con)
    except Exception as exc:
        con.execute(
            "UPDATE analog_search_runs_v3 SET finished_at=current_timestamp,status='failed',"
            "details_json=? WHERE status='running'",
            [json.dumps({"error_type": type(exc).__name__, "error": str(exc)})],
        )
        raise


def analog_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    latest = con.execute(
        "SELECT run_id,cutoff,contexts,analogs FROM analog_search_runs_v3 "
        "WHERE status IN ('completed','completed_insufficient_data') "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"latest": None}
    return {
        "run_id": latest[0],
        "cutoff": latest[1],
        "contexts": latest[2],
        "analogs": latest[3],
        "methods": con.execute(
            "SELECT method,path_window,count(*) FROM analog_method_diagnostics_v3 "
            "WHERE run_id=? GROUP BY 1,2 ORDER BY 1,2",
            [latest[0]],
        ).fetchall(),
    }
