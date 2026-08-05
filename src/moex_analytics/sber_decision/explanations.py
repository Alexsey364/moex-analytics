"""Auditable Russian explanation templates."""


def render(
    status: str, confidence: float, positive: list[str], negative: list[str], conflicts: list[str]
) -> str:
    plus = "; ".join(positive) or "существенных подтверждённых преимуществ недостаточно"
    minus = "; ".join(negative) or "критические риски не выявлены"
    conflict = "; ".join(conflicts) or "существенного конфликта независимых блоков нет"
    return (
        f"Статус: {status}. Горизонт: преимущественно 120–250 торговых дней. "
        f"Положительные факторы: {plus}. Риски: {minus}. Конфликты: {conflict}. "
        f"Макрослой исключён: его добавочная ценность отклонена. Confidence {confidence:.1f}/100; это не вероятность роста и не гарантия."
    )
