"""Strict controlled-import parsers."""

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .models import FundamentalObservation

REQUIRED_COLUMNS = {
    "metric_id",
    "period_start",
    "period_end",
    "report_type",
    "accounting_standard",
    "publication_date",
    "value",
    "unit",
    "source",
    "source_document",
    "revision_id",
}


def parse_frame(frame: pd.DataFrame) -> list[FundamentalObservation]:
    controlled_review = "verification_status" in frame.columns
    if controlled_review:
        frame = frame[frame["verification_status"] == "validated"].copy()
        frame["report_type"] = frame.apply(
            lambda row: (
                "annual"
                if str(row["period_start"])[5:10] == "01-01" and str(row["period_end"])[5:10] == "12-31"
                else "interim"
            ),
            axis=1,
        )
        frame["source"] = "controlled_review"
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    result = []
    for raw in frame.to_dict("records"):
        published = date.fromisoformat(str(raw["publication_date"])[:10])
        result.append(
            FundamentalObservation(
                str(raw["metric_id"]),
                date.fromisoformat(str(raw["period_start"])[:10]),
                date.fromisoformat(str(raw["period_end"])[:10]),
                str(raw["report_type"]),
                str(raw["accounting_standard"]).upper(),
                published,
                datetime.combine(published, time(19), ZoneInfo("Europe/Moscow")),
                float(raw["value"]),
                str(raw["unit"]),
                str(raw["source"]),
                str(raw["source_document"]),
                str(raw["revision_id"]),
            )
        )
    return result


def parse_file(path: Path) -> list[FundamentalObservation]:
    if path.suffix.lower() == ".csv":
        return parse_frame(pd.read_csv(path, sep=None, engine="python"))
    if path.suffix.lower() in {".xlsx", ".xls"}:
        workbook = pd.ExcelFile(path)
        sheet = "Review" if "Review" in workbook.sheet_names else workbook.sheet_names[0]
        return parse_frame(pd.read_excel(workbook, sheet_name=sheet))
    raise ValueError("Only controlled CSV/XLSX imports are supported; PDF requires a verified fixture")
