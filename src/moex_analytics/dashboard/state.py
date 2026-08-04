"""Testable update orchestration, independent from Streamlit widgets."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass
class UpdateResult:
    completed: list[str]
    outputs: dict[str, Any]
    error_step: str | None = None
    error: str | None = None


def run_update_steps(
    steps: Iterable[tuple[str, Callable[[], Any]]],
    on_step: Callable[[str], None] | None = None,
) -> UpdateResult:
    completed, outputs = [], {}
    for name, action in steps:
        if on_step:
            on_step(name)
        try:
            outputs[name] = action()
        except Exception as exc:
            return UpdateResult(completed, outputs, name, str(exc))
        completed.append(name)
    return UpdateResult(completed, outputs)
