"""Deterministic expanding-window baselines and explainable linear models."""

from __future__ import annotations

import numpy as np


def walk_forward_splits(n: int, minimum_train: int, test_size: int, step: int):
    start = minimum_train
    while start < n:
        end = min(start + test_size, n)
        yield np.arange(0, start), np.arange(start, end)
        start += step


class RidgeModel:
    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha

    def fit(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        valid = np.isfinite(x)
        counts = valid.sum(axis=0)
        self.mean_ = np.divide(
            np.where(valid, x, 0).sum(axis=0), counts, out=np.zeros(x.shape[1]), where=counts > 0
        )
        squared = np.where(valid, (x - self.mean_) ** 2, 0).sum(axis=0)
        self.scale_ = np.sqrt(np.divide(squared, counts, out=np.ones(x.shape[1]), where=counts > 0))
        self.scale_[self.scale_ == 0] = 1
        z = np.nan_to_num((x - self.mean_) / self.scale_)
        design = np.column_stack([np.ones(len(z)), z])
        penalty = np.eye(design.shape[1]) * self.alpha
        penalty[0, 0] = 0
        self.coef_ = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        return self

    def predict(self, x):
        z = np.nan_to_num((np.asarray(x, dtype=float) - self.mean_) / self.scale_)
        return np.column_stack([np.ones(len(z)), z]) @ self.coef_


class LogisticModel(RidgeModel):
    def fit(self, x, y):
        super().fit(x, np.asarray(y) * 2 - 1)
        return self

    def predict_proba(self, x):
        raw = self.predict(x)
        probability = 1 / (1 + np.exp(-np.clip(raw, -20, 20)))
        return np.column_stack([1 - probability, probability])


def regression_metrics(actual, predicted) -> dict[str, float]:
    actual, predicted = np.asarray(actual), np.asarray(predicted)
    error = actual - predicted
    variable = len(actual) > 1 and np.std(actual) > 0 and np.std(predicted) > 0
    corr = np.corrcoef(actual, predicted)[0, 1] if variable else np.nan
    rank = (
        np.corrcoef(actual.argsort().argsort(), predicted.argsort().argsort())[0, 1] if variable else np.nan
    )
    return {
        "mae": float(np.mean(abs(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "correlation": float(corr),
        "rank_correlation": float(rank),
        "sign_accuracy": float(np.mean((actual > 0) == (predicted > 0))),
    }


def classification_metrics(actual, probability) -> dict[str, object]:
    actual, probability = np.asarray(actual, dtype=int), np.asarray(probability)
    predicted = probability >= 0.5
    accuracy = np.mean(predicted == actual)
    tpr = np.mean(predicted[actual == 1]) if np.any(actual == 1) else np.nan
    tnr = np.mean(~predicted[actual == 0]) if np.any(actual == 0) else np.nan
    p = np.clip(probability, 1e-9, 1 - 1e-9)
    positives = np.where(actual == 1)[0]
    negatives = np.where(actual == 0)[0]
    auc = np.nan
    if len(positives) and len(negatives):
        order = np.argsort(probability, kind="mergesort")
        ranks = np.empty(len(probability), dtype=float)
        sorted_probability = probability[order]
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and sorted_probability[end] == sorted_probability[start]:
                end += 1
            ranks[order[start:end]] = (start + 1 + end) / 2
            start = end
        rank_sum = ranks[positives].sum()
        auc = float(
            (rank_sum - len(positives) * (len(positives) + 1) / 2) / (len(positives) * len(negatives))
        )
    calibration = []
    for lower in np.linspace(0, 0.8, 5):
        mask = (probability >= lower) & (probability < lower + 0.2)
        if np.any(mask):
            calibration.append(
                {
                    "predicted": float(np.mean(probability[mask])),
                    "observed": float(np.mean(actual[mask])),
                    "count": int(mask.sum()),
                }
            )
    return {
        "accuracy": float(accuracy),
        "balanced_accuracy": float(np.nanmean([tpr, tnr])),
        "roc_auc": auc,
        "brier": float(np.mean((p - actual) ** 2)),
        "log_loss": float(-np.mean(actual * np.log(p) + (1 - actual) * np.log(1 - p))),
        "calibration": calibration,
    }


def empirical_intervals(prediction: float, residuals) -> dict[str, float]:
    residuals = np.asarray(residuals, dtype=float)
    result = {"median": float(prediction + np.median(residuals))}
    for level in (50, 80, 90):
        tail = (100 - level) / 200
        result[f"lower_{level}"] = float(prediction + np.quantile(residuals, tail))
        result[f"upper_{level}"] = float(prediction + np.quantile(residuals, 1 - tail))
    return result


def price_interval(price: float, lower: float, upper: float) -> tuple[float, float]:
    return price * (1 + lower), price * (1 + upper)
