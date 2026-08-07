"""Dashboard navigation groups; basic mode intentionally hides internal pages."""

BASIC_LABELS = (
    "Сегодня",
    "Мой портфель",
    "Акции",
    "Спросить про портфель",
    "Дивиденды",
    "Риски",
    "Сценарии",
    "Обновить данные",
)


def navigation_pages(basic_pages: dict, advanced_pages: dict, advanced: bool = False) -> dict:
    """Return a stable registry with the human dashboard first."""
    return {**basic_pages, **advanced_pages} if advanced else dict(basic_pages)


def validate_basic_labels(pages: dict) -> bool:
    return tuple(pages) == BASIC_LABELS and not any(
        label in pages for label in ("Company Valuation", "Portfolio Action Map", "Regime Risk")
    )
