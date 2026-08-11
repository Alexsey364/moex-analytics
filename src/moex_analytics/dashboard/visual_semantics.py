"""Single accessible visual language for investor-facing dashboard pages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisualToken:
    key: str
    symbol: str
    label: str
    light: str
    dark: str
    direction: str


TOKENS = {
    "positive": VisualToken(
        "positive", "↑", "Положительный подтверждённый сигнал", "#16803c", "#57d17c", "up"
    ),
    "mixed": VisualToken("mixed", "→", "Смешанная картина / ждать", "#9a6700", "#f2cc60", "flat"),
    "caution": VisualToken("caution", "!", "Повышенная осторожность", "#bc4c00", "#ff9d52", "caution"),
    "negative": VisualToken("negative", "↓", "Негатив / не увеличивать", "#cf222e", "#ff7b72", "down"),
    "neutral": VisualToken("neutral", "i", "Информационный / нейтральный", "#0969da", "#58a6ff", "flat"),
    "insufficient": VisualToken(
        "insufficient", "?", "Недостаточно данных / не доказано", "#6e7781", "#b1bac4", "unknown"
    ),
}

STATUS_TO_TOKEN = {
    "GREEN": "positive",
    "LIGHT_GREEN": "positive",
    "YELLOW": "mixed",
    "ORANGE": "caution",
    "RED": "negative",
    "BLUE": "neutral",
    "GRAY": "insufficient",
    "small_positive": "positive",
    "positive": "positive",
    "up": "positive",
    "↑": "positive",
    "small_negative": "negative",
    "negative": "negative",
    "down": "negative",
    "↓": "negative",
    "neutral": "mixed",
    "mixed": "mixed",
    "→": "mixed",
    "unknown": "insufficient",
    "insufficient_data": "insufficient",
    "no_evidence": "insufficient",
}


def token_for(value: object) -> VisualToken:
    key = str(value or "unknown").strip().lower()
    mapped = STATUS_TO_TOKEN.get(str(value or "").upper(), STATUS_TO_TOKEN.get(key, "insufficient"))
    return TOKENS[mapped]


def color_for(value: object, *, dark: bool = False) -> str:
    token = token_for(value)
    return token.dark if dark else token.light


def accessible_label(value: object) -> str:
    token = token_for(value)
    return f"{token.symbol} {token.label}"


def confidence_segments(value: object) -> str:
    if isinstance(value, str):
        levels = {
            "low": 1,
            "низкая": 1,
            "medium": 2,
            "средняя": 2,
            "above_average": 3,
            "выше средней": 3,
            "high": 4,
            "высокая": 4,
        }
        level = levels.get(value.lower(), 1)
    else:
        score = float(value or 0)
        level = 1 if score < 0.35 else 2 if score < 0.6 else 3 if score < 0.8 else 4
    names = ("низкая", "средняя", "выше средней", "высокая")
    return f"{'●' * level}{'○' * (4 - level)} {names[level - 1]}"


def forecast_marker(outcome_status: object, direction_correct=None, neutral_hit=None) -> VisualToken:
    if str(outcome_status or "pending").lower() != "matured":
        return TOKENS["insufficient"]
    neutral_value = str(neutral_hit).lower()
    direction_value = str(direction_correct).lower()
    if neutral_value == "true":
        return TOKENS["mixed"]
    if direction_value == "true":
        return TOKENS["positive"]
    if direction_value == "false":
        return TOKENS["negative"]
    return TOKENS["mixed"]


def theme_css() -> str:
    """Theme-aware CSS variables; charts still carry text, symbols and legends."""
    return """<style>
    :root { --moex-positive:#16803c; --moex-mixed:#9a6700; --moex-caution:#bc4c00;
      --moex-negative:#cf222e; --moex-neutral:#0969da; --moex-insufficient:#6e7781; }
    @media (prefers-color-scheme: dark) { :root { --moex-positive:#57d17c;
      --moex-mixed:#f2cc60; --moex-caution:#ff9d52; --moex-negative:#ff7b72;
      --moex-neutral:#58a6ff; --moex-insufficient:#b1bac4; } }
    .status-card { border:1px solid color-mix(in srgb,currentColor 25%,transparent);
      border-radius:12px; padding:.75rem 1rem; margin:.35rem 0; }
    @media (max-width:900px) { [data-testid="stHorizontalBlock"] { flex-wrap:wrap; } }
    </style>"""
