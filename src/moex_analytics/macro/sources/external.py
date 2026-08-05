"""Metadata-only external markets until licensed PIT-safe feeds are configured."""

from ..models import SeriesDefinition


def definitions() -> list[SeriesDefinition]:
    return [
        SeriesDefinition(
            "external_brent",
            "Brent crude",
            "USD/barrel",
            "trading daily",
            "not configured",
            "",
            None,
            "Requires close timestamp and licence",
            "Unknown",
            False,
            "Excluded from strict tests",
        ),
        SeriesDefinition(
            "external_gold",
            "Gold",
            "USD/ounce",
            "trading daily",
            "not configured",
            "",
            None,
            "Requires close timestamp and licence",
            "Unknown",
            False,
            "Excluded from strict tests",
        ),
    ]
