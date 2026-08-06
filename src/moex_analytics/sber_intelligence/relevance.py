"""Transparent relevance rules."""


def assess(entity: str, event_type: str, official: bool) -> tuple[float, str]:
    if entity == "SBER":
        return (1.0 if official else 0.8), "direct entity match"
    if event_type in {"key_rate", "bank_regulation", "sanctions"}:
        return 0.7, "material indirect banking effect"
    return 0.2, "weak indirect relation"
