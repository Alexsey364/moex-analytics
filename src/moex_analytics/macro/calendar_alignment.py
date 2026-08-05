"""Strict as-of alignment using availability timestamps."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd


def trade_cutoff(trade_date, cutoff: str, timezone: str = "Europe/Moscow") -> datetime:
    return datetime.combine(trade_date, time.fromisoformat(cutoff), ZoneInfo(timezone))


def align_point_in_time(
    sessions: pd.DataFrame, observations: pd.DataFrame, cutoff_column: str = "cutoff"
) -> pd.DataFrame:
    """Backward as-of join; future publications cannot be forward-filled backwards."""
    left = sessions.copy().sort_values(cutoff_column)
    right = observations.copy().sort_values("available_from")
    left[cutoff_column] = pd.to_datetime(left[cutoff_column], utc=True)
    right["available_from"] = pd.to_datetime(right["available_from"], utc=True)
    return pd.merge_asof(
        left,
        right,
        left_on=cutoff_column,
        right_on="available_from",
        direction="backward",
        allow_exact_matches=True,
    )


def external_available_from(close_at: datetime, moex_close_at: datetime) -> datetime:
    """A foreign close after MOEX close becomes usable only after that timestamp."""
    return max(close_at, moex_close_at) if close_at <= moex_close_at else close_at
