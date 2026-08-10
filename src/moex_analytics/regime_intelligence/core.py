"""Train-only market representation, unsupervised regimes and novelty diagnostics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.mixture import GaussianMixture

from moex_analytics.adaptive_learning.core import _build_frame, _macro

from .schema import DDL

VERSION = "regime-intelligence-v2"
INSTRUMENTS = ("X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX")
HORIZONS = (5, 20, 60, 120, 250)
FEATURES = (
    "ret_5",
    "ret_20",
    "ret_60",
    "vol_5",
    "vol_20",
    "vol_60",
    "drawdown_60",
    "breadth_balance",
    "dispersion",
    "turnover_log",
    "rvi_change",
    "rusfar_change",
    "rgbi_change",
    "cny_change",
    "usd_fix_change",
)
MODEL_CANDIDATE_COLUMNS = (
    "run_id",
    "algorithm",
    "k",
    "train_rows",
    "test_rows",
    "silhouette_train",
    "silhouette_test",
    "persistence",
    "min_cluster_share",
    "oos_reproducibility",
    "selection_score",
    "selected",
    "status",
)


@dataclass(frozen=True)
class RegimeCandidateRecord:
    run_id: str
    algorithm: str
    k: int
    train_rows: int
    test_rows: int
    silhouette_train: float
    silhouette_test: float
    persistence: float
    min_cluster_share: float
    oos_reproducibility: float
    selection_score: float
    selected: bool
    status: str


def ensure_schema(con: Any) -> None:
    con.execute(DDL)
    con.execute("ALTER TABLE regime_model_candidates ADD COLUMN IF NOT EXISTS status VARCHAR")
    actual = tuple(row[1] for row in con.execute("PRAGMA table_info('regime_model_candidates')").fetchall())
    if actual != MODEL_CANDIDATE_COLUMNS:
        raise RuntimeError(
            f"regime_model_candidates schema mismatch: expected={MODEL_CANDIDATE_COLUMNS}, actual={actual}"
        )


def _scale_train(train: pd.DataFrame, all_rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    median = train.median()
    scale = (train.quantile(0.75) - train.quantile(0.25)).replace(0, 1).fillna(1)
    train_x = ((train.fillna(median) - median) / scale).clip(-12, 12).to_numpy(float)
    all_x = ((all_rows.fillna(median) - median) / scale).clip(-12, 12).to_numpy(float)
    return train_x, all_x


def _transition_matrix(labels: np.ndarray, k: int) -> np.ndarray:
    counts = np.ones((k, k), dtype=float)
    for left, right in pairwise(labels):
        counts[int(left), int(right)] += 1
    return counts / counts.sum(axis=1, keepdims=True)


def _viterbi(emission_log: np.ndarray, transitions: np.ndarray) -> np.ndarray:
    """Decode a Gaussian-emission Markov chain without using future returns."""
    n, k = emission_log.shape
    score = np.full((n, k), -np.inf)
    back = np.zeros((n, k), dtype=int)
    score[0] = emission_log[0] - np.log(k)
    log_transition = np.log(np.maximum(transitions, 1e-12))
    for index in range(1, n):
        candidates = score[index - 1][:, None] + log_transition
        back[index] = np.argmax(candidates, axis=0)
        score[index] = emission_log[index] + np.max(candidates, axis=0)
    labels = np.zeros(n, dtype=int)
    labels[-1] = int(np.argmax(score[-1]))
    for index in range(n - 2, -1, -1):
        labels[index] = back[index + 1, labels[index + 1]]
    return labels


def fit_candidate(train_x: np.ndarray, all_x: np.ndarray, algorithm: str, k: int) -> np.ndarray:
    if algorithm == "kmeans":
        return KMeans(n_clusters=k, random_state=42, n_init=20).fit(train_x).predict(all_x)
    mixture = GaussianMixture(n_components=k, covariance_type="diag", random_state=42, n_init=5)
    initial = mixture.fit(train_x).predict(train_x)
    if algorithm == "gaussian_mixture":
        return mixture.predict(all_x)
    transitions = _transition_matrix(initial, k)
    return _viterbi(mixture._estimate_weighted_log_prob(all_x), transitions)


def _candidate_metrics(train_x, test_x, labels, split, k) -> dict[str, float]:
    train_labels, test_labels = labels[:split], labels[split:]
    sil_train = silhouette_score(train_x, train_labels) if len(set(train_labels)) > 1 else -1.0
    sil_test = (
        silhouette_score(test_x, test_labels) if len(set(test_labels)) > 1 and len(test_x) > k else -1.0
    )
    persistence = float(np.mean(labels[1:] == labels[:-1]))
    shares = np.bincount(labels, minlength=k) / len(labels)
    min_share = float(shares.min())
    reproducibility = max(0.0, 1 - abs(sil_train - sil_test))
    score = 0.35 * sil_test + 0.25 * persistence + 0.25 * reproducibility + 0.15 * min(1, min_share * k)
    return {
        "silhouette_train": sil_train,
        "silhouette_test": sil_test,
        "persistence": persistence,
        "min_cluster_share": min_share,
        "oos_reproducibility": reproducibility,
        "selection_score": score,
    }


def _durations(labels: np.ndarray) -> np.ndarray:
    result = np.ones(len(labels), dtype=int)
    for index in range(1, len(labels)):
        result[index] = result[index - 1] + 1 if labels[index] == labels[index - 1] else 1
    return result


def _novelty(train_x: np.ndarray, all_x: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[str]]:
    center = np.median(train_x, axis=0)
    distances = np.sqrt(np.mean((all_x - center) ** 2, axis=1))
    reference = np.sort(np.sqrt(np.mean((train_x - center) ** 2, axis=1)))
    percentiles = np.searchsorted(reference, distances, side="right") / len(reference)
    labels = [
        "familiar"
        if p < 0.8
        else "somewhat_unusual"
        if p < 0.95
        else "rare"
        if p < 0.995
        else "historically_novel"
        for p in percentiles
    ]
    return distances, percentiles, labels


def _insert_frame(con, table: str, frame: pd.DataFrame) -> None:
    name = f"incoming_{table}"
    columns = list(frame.columns)
    quoted = ",".join(f'"{column}"' for column in columns)
    con.register(name, frame)
    con.execute(f"INSERT OR REPLACE INTO {table} ({quoted}) SELECT {quoted} FROM {name}")
    con.unregister(name)


def save_candidate(con: Any, record: RegimeCandidateRecord) -> None:
    values = asdict(record)
    columns = list(values)
    names = ",".join(columns)
    placeholders = ",".join("?" for _ in columns)
    con.execute(
        f"INSERT OR REPLACE INTO regime_model_candidates ({names}) VALUES ({placeholders})",
        list(values.values()),
    )


def run_regime_intelligence(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    macro = _macro(con)
    market = (
        _build_frame(con, "IMOEX", macro)
        if con.execute(
            "SELECT count(*) FROM canonical_daily_prices WHERE canonical_secid='IMOEX'"
        ).fetchone()[0]
        else _build_frame(con, "SBERP", macro)
    )
    features = [name for name in FEATURES if name in market and market[name].notna().sum() >= 500]
    state = market[features].dropna(thresh=max(4, len(features) // 2)).copy()
    split = int(len(state) * 0.8)
    if split < 500 or len(state) - split < 100:
        raise ValueError("insufficient chronological market history for Stage 43")
    train_x, all_x = _scale_train(state.iloc[:split], state)
    test_x = all_x[split:]
    cutoff = state.index.max().date()
    train_end = state.index[split - 1].date()
    run_id = hashlib.sha256(f"{cutoff}|{train_end}|{VERSION}|{len(state)}".encode()).hexdigest()[:20]
    con.execute("DELETE FROM regime_intelligence_runs WHERE run_id=?", [run_id])
    for table in (
        "regime_market_state_vectors",
        "regime_issuer_state_vectors",
        "regime_model_candidates",
        "regime_timeline_v2",
        "regime_transitions_v2",
        "regime_conditional_effects",
    ):
        con.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
    distances, percentiles, novelty = _novelty(train_x, all_x)
    candidates = []
    assignments = {}
    for algorithm in ("kmeans", "gaussian_mixture", "gaussian_hmm"):
        for k in range(2, 7):
            labels = fit_candidate(train_x, all_x, algorithm, k)
            metrics = _candidate_metrics(train_x, test_x, labels, split, k)
            valid = metrics["min_cluster_share"] >= 0.02 and metrics["silhouette_test"] > -0.05
            candidates.append((algorithm, k, labels, metrics, valid))
    eligible = [item for item in candidates if item[4]]
    if not eligible:
        raise ValueError("no stable regime candidate passed minimum diagnostics")
    selected = max(eligible, key=lambda item: item[3]["selection_score"])
    selected_algorithm, selected_k = selected[0], selected[1]
    for algorithm, k, labels, metrics, valid in candidates:
        chosen = algorithm == selected_algorithm and k == selected_k
        assignments[(algorithm, k)] = labels
        save_candidate(
            con,
            RegimeCandidateRecord(
                run_id=run_id,
                algorithm=algorithm,
                k=k,
                train_rows=split,
                test_rows=len(state) - split,
                silhouette_train=metrics["silhouette_train"],
                silhouette_test=metrics["silhouette_test"],
                persistence=metrics["persistence"],
                min_cluster_share=metrics["min_cluster_share"],
                oos_reproducibility=metrics["oos_reproducibility"],
                selection_score=metrics["selection_score"],
                selected=chosen,
                status="stable" if valid else "rejected_unstable",
            ),
        )
        durations = _durations(labels)
        timeline = pd.DataFrame(
            {
                "run_id": run_id,
                "trade_date": state.index.date,
                "algorithm": algorithm,
                "k": k,
                "regime": labels,
                "regime_duration": durations,
                "novelty_status": novelty,
                "selected": chosen,
            }
        )
        _insert_frame(con, "regime_timeline_v2", timeline)
        matrix = _transition_matrix(labels, k)
        for left in range(k):
            for right in range(k):
                observations = int(np.sum((labels[:-1] == left) & (labels[1:] == right)))
                con.execute(
                    "INSERT OR REPLACE INTO regime_transitions_v2 "
                    "(run_id,algorithm,k,from_regime,to_regime,observations,"
                    "transition_frequency,selected) VALUES (?,?,?,?,?,?,?,?)",
                    [run_id, algorithm, k, left, right, observations, float(matrix[left, right]), chosen],
                )
    market_rows = pd.DataFrame({"run_id": run_id, "trade_date": state.index.date})
    names = {
        "ret_5": "ret_5",
        "ret_20": "ret_20",
        "ret_60": "ret_60",
        "vol_5": "volatility_5",
        "vol_20": "volatility_20",
        "vol_60": "volatility_60",
        "drawdown_60": "drawdown_60",
        "breadth_balance": "breadth_balance",
        "dispersion": "dispersion",
        "turnover_log": "turnover_log",
        "rvi_change": "rvi_change",
        "rusfar_change": "rusfar_change",
        "rgbi_change": "rgbi_change",
        "cny_change": "cny_change",
        "usd_fix_change": "usd_change",
    }
    for source, target in names.items():
        market_rows[target] = state[source].values if source in state else np.nan
    market_rows["novelty_distance"] = distances
    market_rows["novelty_percentile"] = percentiles
    market_rows["novelty_status"] = novelty
    _insert_frame(con, "regime_market_state_vectors", market_rows)
    selected_labels = assignments[(selected_algorithm, selected_k)]
    label_by_date = dict(zip(state.index, selected_labels, strict=False))
    issuer_count = 0
    for secid in INSTRUMENTS:
        frame = _build_frame(con, secid, macro).reindex(state.index)
        issuer = pd.DataFrame({"run_id": run_id, "trade_date": state.index.date, "secid": secid})
        for source, target in (
            ("ret_5", "ret_5"),
            ("ret_20", "ret_20"),
            ("ret_60", "ret_60"),
            ("vol_20", "volatility_20"),
            ("drawdown_60", "drawdown_60"),
            ("turnover_log", "turnover_log"),
            ("relative_20", "relative_20"),
            ("breadth_balance", "breadth_balance"),
            ("stress", "market_stress"),
        ):
            issuer[target] = frame[source].values if source in frame else np.nan
        issuer["data_coverage"] = issuer.drop(columns=["run_id", "trade_date", "secid"]).notna().mean(axis=1)
        _insert_frame(con, "regime_issuer_state_vectors", issuer)
        issuer_count += len(issuer)
        prices = frame.close.dropna()
        for horizon in HORIZONS:
            future = prices.shift(-horizon) / prices - 1
            joined = pd.DataFrame(
                {"return": future, "regime": [label_by_date.get(x, np.nan) for x in prices.index]}
            ).dropna()
            for regime, group in joined.groupby("regime"):
                values = group["return"].to_numpy(float)
                con.execute(
                    "INSERT OR REPLACE INTO regime_conditional_effects "
                    "(run_id,secid,horizon,regime,observations,mean_return,median_return,"
                    "volatility,positive_fraction,max_drawdown,status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        run_id,
                        secid,
                        horizon,
                        int(regime),
                        len(values),
                        float(np.mean(values)),
                        float(np.median(values)),
                        float(np.std(values)),
                        float(np.mean(values > 0)),
                        float(np.min(values)),
                        "historical_association_not_causality",
                    ],
                )
    current_regime = int(selected_labels[-1])
    details = {
        "algorithms": ["kmeans", "gaussian_mixture", "gaussian_hmm"],
        "k": [2, 3, 4, 5, 6],
        "production_changes": 0,
        "probability_gate_changes": 0,
    }
    con.execute(
        "INSERT OR REPLACE INTO regime_intelligence_runs "
        "(run_id,created_at,finished_at,status,cutoff,train_end,rows_count,features_count,"
        "selected_model,selected_k,methodology_version,details_json) "
        "VALUES (?,current_timestamp,current_timestamp,'completed',?,?,?,?,?,?,?,?)",
        [
            run_id,
            cutoff,
            train_end,
            len(state),
            len(features),
            selected_algorithm,
            selected_k,
            VERSION,
            json.dumps(details),
        ],
    )
    return {
        "run_id": run_id,
        "rows": len(state),
        "issuer_rows": issuer_count,
        "features": len(features),
        "selected_model": selected_algorithm,
        "selected_k": selected_k,
        "current_regime": current_regime,
        "current_novelty": novelty[-1],
        "train_end": train_end,
        "cutoff": cutoff,
    }


def regime_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    latest = con.execute(
        "SELECT run_id,cutoff,train_end,selected_model,selected_k "
        "FROM regime_intelligence_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return {"latest": None}
    run_id, cutoff, train_end, model, k = latest
    current = con.execute(
        "SELECT regime,regime_duration,novelty_status FROM regime_timeline_v2 "
        "WHERE run_id=? AND selected ORDER BY trade_date DESC LIMIT 1",
        [run_id],
    ).fetchone()
    return {
        "run_id": run_id,
        "cutoff": cutoff,
        "train_end": train_end,
        "selected_model": model,
        "selected_k": k,
        "current_regime": current[0],
        "duration": current[1],
        "novelty": current[2],
    }
