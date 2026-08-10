"""Dashboard navigation groups; basic mode intentionally hides internal pages."""

BASIC_LABELS = (
    "Сегодня",
    "Состояние рынка",
    "Мой портфель",
    "Куда вложить пополнение",
    "Акции",
    "Спросить про портфель",
    "Как программа прогнозирует",
    "Как программа учится",
    "Качество прогнозов",
    "Реальная проверка",
    "Когда начнётся реальная проверка",
    "Что программа уже доказала",
    "Дивиденды",
    "Риски",
    "Сценарии",
    "Обновить данные",
    "История обновлений",
    "Качество данных",
)

ADVANCED_GROUPS = {
    "Данные": ("данн", "база", "качество", "обнов", "источник"),
    "Фундаментал": ("fundamental", "valuation", "дивид", "мсфо"),
    "Модели": ("модел", "model", "прогноз", "regime", "режим"),
    "Alpha Research": ("alpha", "feature", "interaction"),
    "Backtest": ("backtest", "walk-forward", "проверка"),
    "Portfolio Research": ("portfolio", "портфел"),
    "Diagnostics": (),
}


def group_advanced_pages(pages: dict) -> dict[str, dict]:
    groups = {name: {} for name in ADVANCED_GROUPS}
    for label, renderer in pages.items():
        lowered = label.lower()
        group = next(
            (
                name
                for name, fragments in ADVANCED_GROUPS.items()
                if fragments and any(fragment in lowered for fragment in fragments)
            ),
            "Diagnostics",
        )
        groups[group][label] = renderer
    return groups


def navigation_pages(basic_pages: dict, advanced_pages: dict, advanced: bool = False) -> dict:
    """Return a stable registry with the human dashboard first."""
    return {**basic_pages, **advanced_pages} if advanced else dict(basic_pages)


def validate_basic_labels(pages: dict) -> bool:
    return tuple(pages) == BASIC_LABELS and not any(
        label in pages for label in ("Company Valuation", "Portfolio Action Map", "Regime Risk")
    )
