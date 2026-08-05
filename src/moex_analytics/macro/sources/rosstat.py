"""Rosstat metadata: unsafe series are deliberately excluded from strict PIT models."""

from datetime import date

from ..models import SeriesDefinition


def definitions() -> list[SeriesDefinition]:
    endpoint = "https://rosstat.gov.ru/statistics/price/"
    note = "Historical values exist, but verified historical release timestamps/vintages are unavailable"
    return [
        SeriesDefinition(
            "rosstat_cpi_yoy",
            "Russian CPI year-on-year",
            "%",
            "monthly",
            "Rosstat",
            endpoint,
            date(1991, 1, 1),
            "Use only explicit release timestamp",
            "Final at first publication",
            False,
            note,
        ),
        SeriesDefinition(
            "rosstat_cpi_mom",
            "Russian CPI month-on-month",
            "%",
            "monthly",
            "Rosstat",
            endpoint,
            date(1991, 1, 1),
            "Use only explicit release timestamp",
            "Final at first publication",
            False,
            note,
        ),
    ]
