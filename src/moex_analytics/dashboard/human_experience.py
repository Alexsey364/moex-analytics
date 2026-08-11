"""Russian-only presentation helpers for BASIC investor pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

SECURITY_NAMES = {
    "SBERP": "Сбербанк ап",
    "LKOH": "ЛУКОЙЛ",
    "TRNFP": "Транснефть ап",
    "TATNP": "Татнефть ап",
    "LSNGP": "Россети Ленэнерго ап",
    "MTSS": "МТС",
    "PHOR": "ФосАгро",
    "MOEX": "Московская биржа",
    "X5": "X5",
}

INTERNAL_STATUS_RU = {
    "NO_EVIDENCE": "⚪ Преимущество не подтверждено",
    "WEAK_EVIDENCE": "🟡 Есть слабые признаки",
    "SHADOW_CANDIDATE": "🔵 Исследовательская модель, ещё проверяется",
    "requires_more_history": "⚪ Нужно больше истории",
    "insufficient_history": "⚪ Нужно больше истории",
    "insufficient_live_sample": "⚪ Реальная проверка только началась",
    "research_oos": "историческая проверка вне обучающего периода",
    "invalid_incomplete_universe": "⚪ Недостаточно полного набора бумаг",
    "no_evidence": "⚪ Преимущество не подтверждено",
    "unknown": "⚪ Недостаточно данных",
    "stress": "🟠 Напряжённый рынок",
    "стрессовый": "🟠 Напряжённый рынок",
    "normal": "🟡 Неоднозначный рынок",
}

FORBIDDEN_BASIC_TERMS = (
    "Rank IC",
    "SHADOW_CANDIDATE",
    "NO_EVIDENCE",
    "WEAK_EVIDENCE",
    "research_oos",
    "effective N",
    "bootstrap",
    "quantile",
    "abstain",
)

HORIZON_NAMES = {1: "1 день", 5: "1 неделя", 20: "1 месяц", 60: "3 месяца", 120: "6 месяцев", 250: "1 год"}


@dataclass(frozen=True)
class DecisionBlock:
    name: str
    status: str
    explanation: str
    effect: str
    affects_decision: bool
    strength: str


def horizon_state(value: object) -> str:
    raw = str(value or "").lower()
    if any(token in raw for token in ("позитив", "сильнее", "small_positive", "↑")):
        return "🟢 сильнее альтернатив"
    if any(token in raw for token in ("негатив", "слабее", "small_negative", "↓")):
        return "🟠 слабее альтернатив"
    if any(token in raw for token in ("нейтрал", "mixed", "→")):
        return "🟡 смешанная картина"
    return "⚪ данных мало"


def action_text(action_group: object) -> str:
    return {
        "consider": "🟢 Можно рассматривать небольшой транш",
        "wait": "🟡 Держать и наблюдать",
        "do_not_increase": "🟠 Пока не увеличивать",
        "insufficient_data": "⚪ Недостаточно данных",
    }.get(str(action_group), "🟡 Сильного сигнала нет")


def security_name(secid: str) -> str:
    return SECURITY_NAMES.get(secid, secid)


def human_status(value: object) -> str:
    raw = str(value or "unknown")
    return INTERNAL_STATUS_RU.get(raw, INTERNAL_STATUS_RU.get(raw.lower(), raw))


def rubles(value: float | int | None, decimals: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",") + " ₽"


def percent(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.{decimals}f}%".replace(".", ",").replace("-", "−")


def russian_date(value: date | datetime | None) -> str:
    if value is None:
        return "—"
    months = (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )
    return f"{value.day} {months[value.month - 1]} {value.year}"


def short_date(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    raw = str(value)
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m.%Y")
    except ValueError:
        return raw


def portfolio_verdict(statuses: list[str]) -> tuple[str, str]:
    if any(value == "RED" for value in statuses):
        return "🔴 Риск портфеля повышен", "Сначала проверьте концентрацию и основные источники риска."
    if any(value in {"GREEN", "LIGHT_GREEN"} for value in statuses):
        return (
            "🟢 Есть несколько интересных возможностей",
            "Пополнение допустимо только небольшими траншами и в пределах рассчитанных ограничений.",
        )
    if any(value == "ORANGE" for value in statuses):
        return (
            "🟠 Нужна повышенная осторожность",
            "Срочных покупок программа не предлагает; часть позиций лучше пока не увеличивать.",
        )
    if statuses and all(value == "GRAY" for value in statuses):
        return (
            "⚪ Недостаточно подтверждённых данных",
            "Программа продолжит собирать историю; сильных выводов пока нет.",
        )
    return (
        "🟡 Ситуация спокойная, но сильного сигнала на покупки нет",
        "Портфель выглядит устойчиво. Срочных действий программа не видит; "
        "новые деньги разумно держать в резерве до подтверждения преимущества.",
    )
