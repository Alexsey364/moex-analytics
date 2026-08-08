"""Explainable visual statuses and conservative contribution planning."""

from __future__ import annotations

from dataclasses import dataclass

STATUS = {
    "GREEN": ("🟢", "Можно рассматривать пополнение", 0),
    "LIGHT_GREEN": ("🟢", "Допустим небольшой транш", 1),
    "YELLOW": ("🟡", "Лучше подождать", 2),
    "BLUE": ("🔵", "Наблюдать", 3),
    "ORANGE": ("🟠", "Повышенная осторожность", 4),
    "RED": ("🔴", "Сейчас не увеличивать", 5),
    "GRAY": ("⚪", "Недостаточно данных", 6),
}


def visual_status(
    *,
    action_group: str,
    confidence: float,
    data_status: str,
    fundamental_status: str = "unknown",
    research_status: str = "unknown",
    weight: float = 0.0,
    risk_contribution: float = 0.0,
) -> str:
    """Map existing evidence to a status with explicit conservative overrides."""
    if weight >= 0.30 or risk_contribution >= 0.30 or action_group == "do_not_increase":
        return "RED"
    if action_group == "insufficient_data":
        return "GRAY"
    if data_status not in {"sufficient", "validated_current"} or confidence < 35:
        return "GRAY"
    if fundamental_status in {"missing", "insufficient_data", "source_access_problem"}:
        return "ORANGE"
    if action_group == "consider":
        if research_status == "rejected":
            return "YELLOW"
        return "GREEN" if confidence >= 70 else "LIGHT_GREEN"
    if action_group == "wait":
        return "YELLOW"
    return "BLUE"


def status_label(status: str) -> str:
    symbol, text, _ = STATUS[status]
    return f"{symbol} {text}"


def horizon_label(text: str) -> str:
    lowered = text.lower()
    if "недостаточно" in lowered or text.startswith("?"):
        return "⚪ Недостаточно данных"
    if "позитив" in lowered or text.startswith("↑"):
        return "🟢 Позитивный перевес"
    if "негатив" in lowered or text.startswith("↓"):
        return "🔴 Негативный перевес"
    return "🟡 Нейтрально"


def confidence_dots(label: str) -> str:
    count = {"низкая": 1, "средняя": 2, "выше средней": 3, "высокая": 4}.get(label, 1)
    return "●" * count + "○" * (4 - count) + f" {label.capitalize()}"


def status_change(current: str, previous: str | None) -> str:
    if previous is None:
        return "→ нет сравнения"
    current_rank, previous_rank = STATUS[current][2], STATUS[previous][2]
    if current_rank < previous_rank:
        return "↑ улучшилось"
    if current_rank > previous_rank:
        return "↓ ухудшилось"
    return "→ без изменений"


@dataclass(frozen=True)
class AllocationPlan:
    rows: list[dict]
    invested: float
    reserve: float


def plan_allocation(amount: float, candidates: list[dict]) -> AllocationPlan:
    """Allocate at most 30% and only to supported candidates, rounded to lots."""
    if amount <= 0:
        raise ValueError("Сумма пополнения должна быть положительной")
    eligible = [
        item
        for item in candidates
        if item["status"] in {"GREEN", "LIGHT_GREEN"}
        and item.get("allow_buy", True)
        and item.get("price", 0) > 0
        and item.get("liquidity_ok", False)
    ]
    budget = amount * (0.30 if eligible else 0.0)
    share, rows = (budget / len(eligible) if eligible else 0.0), []
    for item in eligible:
        lot = max(1, int(item.get("lot_size", 1)))
        lot_cost = lot * float(item["price"])
        lots = int(share // lot_cost)
        cost = lots * lot_cost
        if lots:
            rows.append(
                {
                    **item,
                    "lots": lots,
                    "quantity": lots * lot,
                    "amount": cost,
                    "allocation_share": cost / amount,
                }
            )
    invested = sum(row["amount"] for row in rows)
    return AllocationPlan(rows, invested, amount - invested)
