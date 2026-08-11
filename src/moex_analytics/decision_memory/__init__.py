"""Immutable memory of what the investor-facing system actually said."""

from .core import capture_decision_snapshot, latest_changes

__all__ = ["capture_decision_snapshot", "latest_changes"]
