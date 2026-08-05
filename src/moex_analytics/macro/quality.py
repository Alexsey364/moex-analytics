"""Macro data quality checks that report rather than silently repair."""

from __future__ import annotations

import pandas as pd


def inspect_observations(frame: pd.DataFrame) -> list[dict]:
    issues: list[dict] = []
    if frame.empty:
        return issues
    keys = ["series_id", "observation_date", "vintage"]
    for row in frame[frame.duplicated(keys, keep=False)].itertuples():
        issues.append(
            {
                "series_id": row.series_id,
                "observation_date": row.observation_date,
                "issue_type": "duplicate",
                "severity": "error",
            }
        )
    for row in frame.itertuples():
        if pd.isna(row.value):
            issues.append(
                {
                    "series_id": row.series_id,
                    "observation_date": row.observation_date,
                    "issue_type": "missing_value",
                    "severity": "warning",
                }
            )
        if row.observation_date > row.release_date:
            issues.append(
                {
                    "series_id": row.series_id,
                    "observation_date": row.observation_date,
                    "issue_type": "observation_after_release",
                    "severity": "error",
                }
            )
        if pd.Timestamp(row.available_from).date() < row.release_date:
            issues.append(
                {
                    "series_id": row.series_id,
                    "observation_date": row.observation_date,
                    "issue_type": "available_before_release",
                    "severity": "error",
                }
            )
    return issues
