"""Deterministic expanding-window baselines and explainable linear models."""

from __future__ import annotations

import numpy as np


class LeakageSafeTransformer:
    """Train-only imputation, clipping and scaling for chronological validation."""

    def __init__(self, method: str = "standard", winsor: tuple[float, float] | None = None):
        self.method = method
        self.winsor = winsor

    def fit(self, x):
        values = np.asarray(x, dtype=float)
        if self.method in {"standard", "none"}:
            valid = np.isfinite(values)
            counts = valid.sum(axis=0)
            self.impute_ = np.divide(
                np.where(valid, values, 0).sum(axis=0),
                counts,
                out=np.zeros(values.shape[1]),
                where=counts > 0,
            )
        else:
            with np.errstate(all="ignore"):
                self.impute_ = np.nanmedian(values, axis=0)
        self.impute_ = np.nan_to_num(self.impute_)
        filled = np.where(np.isfinite(values), values, self.impute_)
        if self.winsor:
            self.lower_ = np.quantile(filled, self.winsor[0], axis=0)
            self.upper_ = np.quantile(filled, self.winsor[1], axis=0)
            filled = np.clip(filled, self.lower_, self.upper_)
        if self.method == "standard":
            self.center_ = self.impute_.copy()
            squared = np.where(np.isfinite(values), (values - self.center_) ** 2, 0).sum(axis=0)
            counts = np.isfinite(values).sum(axis=0)
            self.scale_ = np.sqrt(np.divide(squared, counts, out=np.ones(values.shape[1]), where=counts > 0))
        elif self.method == "robust":
            self.center_ = np.median(filled, axis=0)
            q25, q75 = np.quantile(filled, [0.25, 0.75], axis=0)
            self.scale_ = q75 - q25
        elif self.method == "rank":
            self.sorted_ = [np.sort(filled[:, i]) for i in range(filled.shape[1])]
            self.center_ = np.zeros(filled.shape[1])
            self.scale_ = np.ones(filled.shape[1])
        elif self.method == "none":
            self.center_ = np.zeros(filled.shape[1])
            self.scale_ = np.ones(filled.shape[1])
        elif self.method != "none":
            self.center_ = np.mean(filled, axis=0)
            self.scale_ = np.std(filled, axis=0)
        self.scale_ = np.where(self.scale_ == 0, 1.0, self.scale_)
        return self

    def transform(self, x):
        values = np.asarray(x, dtype=float)
        filled = np.where(np.isfinite(values), values, self.impute_)
        if self.winsor:
            filled = np.clip(filled, self.lower_, self.upper_)
        if self.method == "rank":
            return np.column_stack(
                [np.searchsorted(s, filled[:, i], side="right") / len(s) for i, s in enumerate(self.sorted_)]
            )
        return (filled - self.center_) / self.scale_


def walk_forward_splits(n: int, minimum_train: int, test_size: int, step: int):
    start = minimum_train
    while start < n:
        end = min(start + test_size, n)
        yield np.arange(0, start), np.arange(start, end)
        start += step


class RidgeModel:
    def __init__(self, alpha: float = 1.0, transformer: LeakageSafeTransformer | None = None) -> None:
        self.alpha = alpha
        self.transformer = transformer or LeakageSafeTransformer()

    def fit(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        z = self.transformer.fit(x).transform(x)
        self.mean_ = self.transformer.center_
        self.scale_ = self.transformer.scale_
        design = np.column_stack([np.ones(len(z)), z])
        penalty = np.eye(design.shape[1]) * self.alpha
        penalty[0, 0] = 0
        self.coef_ = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        return self

    def predict(self, x):
        z = self.transformer.transform(x)
        return np.column_stack([np.ones(len(z)), z]) @ self.coef_


class ElasticNetModel(RidgeModel):
    """Small deterministic coordinate-descent linear model for audit experiments."""

    def __init__(self, alpha: float = 0.01, l1_ratio: float = 1.0, **kwargs) -> None:
        super().__init__(alpha=alpha, **kwargs)
        self.l1_ratio = l1_ratio

    def fit(self, x, y):
        z = self.transformer.fit(x).transform(x)
        y = np.asarray(y, dtype=float)
        self.intercept_ = float(np.mean(y))
        centered = y - self.intercept_
        coef = np.zeros(z.shape[1])
        fitted = np.zeros(len(z))
        for _ in range(30):
            previous = coef.copy()
            for j in range(z.shape[1]):
                residual = centered - fitted + z[:, j] * coef[j]
                rho = float(z[:, j] @ residual) / len(z)
                threshold = self.alpha * self.l1_ratio
                denominator = max(float(np.mean(z[:, j] ** 2)) + self.alpha * (1 - self.l1_ratio), 1e-12)
                updated = np.sign(rho) * max(abs(rho) - threshold, 0) / denominator
                fitted += z[:, j] * (updated - coef[j])
                coef[j] = updated
            if np.max(np.abs(coef - previous)) < 1e-7:
                break
        self.coef_ = np.r_[self.intercept_, coef]
        self.mean_ = self.transformer.center_
        self.scale_ = self.transformer.scale_
        return self


class LogisticModel(RidgeModel):
    def __init__(
        self,
        alpha: float = 0.01,
        penalty: str = "l2",
        transformer: LeakageSafeTransformer | None = None,
    ) -> None:
        super().__init__(alpha=alpha, transformer=transformer)
        self.penalty = penalty

    def fit(self, x, y):
        z = self.transformer.fit(x).transform(x)
        y = np.asarray(y, dtype=float)
        coef = np.zeros(z.shape[1])
        intercept = 0.0
        learning_rate = 0.1
        for _ in range(300):
            probability = 1 / (1 + np.exp(-np.clip(intercept + z @ coef, -20, 20)))
            error = probability - y
            intercept -= learning_rate * np.mean(error)
            updated = coef - learning_rate * (z.T @ error / len(z))
            if self.penalty == "l1":
                coef = np.sign(updated) * np.maximum(abs(updated) - learning_rate * self.alpha, 0)
            else:
                coef = updated / (1 + learning_rate * self.alpha)
        self.coef_ = np.r_[intercept, coef]
        self.mean_ = self.transformer.center_
        self.scale_ = self.transformer.scale_
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


def nested_time_cv(x, y, candidates: list[dict], minimum_train: int = 250) -> dict:
    """Select parameters using expanding inner folds; chronological order is never shuffled."""
    if not candidates:
        raise ValueError("At least one candidate is required")
    best, best_error = candidates[0], np.inf
    splits = list(walk_forward_splits(len(y), minimum_train, max(20, len(y) // 8), max(20, len(y) // 8)))
    if not splits:
        return best
    for candidate in candidates:
        errors = []
        for train_idx, test_idx in splits:
            model = ElasticNetModel(**candidate).fit(np.asarray(x)[train_idx], np.asarray(y)[train_idx])
            errors.append(np.mean((np.asarray(y)[test_idx] - model.predict(np.asarray(x)[test_idx])) ** 2))
        score = float(np.mean(errors))
        if score < best_error:
            best, best_error = candidate, score
    return best
