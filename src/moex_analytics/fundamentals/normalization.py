"""Auditable unit normalization."""


def normalize(value: float, unit: str) -> tuple[float, str, str]:
    key = unit.strip().lower().replace(".", "")
    if key in {"тыс руб", "тыс рублей"}:
        return value * 1000, "RUB", "thousand_rub_to_rub"
    if key in {"млн руб", "млн рублей"}:
        return value * 1_000_000, "RUB", "million_rub_to_rub"
    if key in {"млрд руб", "млрд рублей"}:
        return value * 1_000_000_000, "RUB", "billion_rub_to_rub"
    if key in {"%", "процент"}:
        return value / 100, "ratio", "percent_to_ratio"
    if key in {"руб", "rub"}:
        return value, "RUB", "identity"
    raise ValueError(f"Unsupported unit: {unit}")
