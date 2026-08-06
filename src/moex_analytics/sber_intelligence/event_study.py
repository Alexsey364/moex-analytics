"""Grouped event-study statistics."""

import statistics


def summarize(values: list[float]) -> dict:
    n = len(values)
    if not n:
        return {"sample_size": 0, "quality": "insufficient_data"}
    ordered = sorted(values)
    q = statistics.quantiles(ordered, n=4) if n >= 4 else [ordered[0], ordered[-1], ordered[-1]]
    quality = (
        "insufficient_data" if n < 10 else "very_weak" if n < 20 else "limited" if n < 50 else "acceptable"
    )
    return {
        "sample_size": n,
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "positive_frequency": sum(x > 0 for x in values) / n,
        "q25": q[0],
        "q75": q[2],
        "best": max(values),
        "worst": min(values),
        "quality": quality,
    }
