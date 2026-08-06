"""Actual-versus-consensus surprise calculations."""


def calculate(actual: float, consensus: float | None, sample_size: int, std: float | None = None) -> dict:
    if consensus is None:
        return {
            "actual": actual,
            "consensus": None,
            "difference": None,
            "percentage": None,
            "standardized": None,
            "direction": None,
            "confidence": 0,
        }
    diff = actual - consensus
    pct = diff / abs(consensus) if consensus else None
    standardized = diff / std if std and std > 0 else None
    direction = "positive" if diff > 0 else "negative" if diff < 0 else "neutral"
    return {
        "actual": actual,
        "consensus": consensus,
        "difference": diff,
        "percentage": pct,
        "standardized": standardized,
        "direction": direction,
        "confidence": min(100, sample_size * 10),
    }
