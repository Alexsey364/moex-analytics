"""Deterministic decision hierarchy; no opaque aggregate score."""

from .models import Decision, Evidence

FAVOURABLE = {"valuation", "business_quality", "dividend"}


def decide(blocks: list[Evidence], *, critical_error: bool = False) -> Decision:
    by_id = {b.block_id: b for b in blocks}
    data = by_id.get("data_quality")
    if critical_error or not data or data.confidence < 35:
        return Decision("недостаточно данных", 250, min(data.confidence if data else 0, 30), 0)
    conflicts = []
    fundamental = min((by_id.get(x, Evidence(x, 0, 0, "missing")).score for x in FAVOURABLE), default=0)
    technical = by_id.get("technical", Evidence("technical", 0, 0, "missing")).score
    if fundamental > 0.15 and technical < -0.15:
        conflicts.append("положительный фундаментал расходится с отрицательным technical")
    valuation = by_id.get("valuation", Evidence("valuation", 0, 0, "missing")).score
    risk = by_id.get("risk", Evidence("risk", 0, 0, "missing")).score
    if risk < -0.65:
        status, fraction = "высокий риск — покупку отложить", 0
    elif valuation < -0.25:
        status, fraction = "ждать более привлекательной цены", 0
    elif fundamental > 0.25 and valuation > 0.2 and technical >= -0.2:
        status, fraction = "допустима поэтапная покупка", 0.25
    elif fundamental > 0.1:
        status, fraction = "допустима небольшая начальная позиция", 0.1
    else:
        status, fraction = "наблюдать", 0
    confidence = sum(b.confidence for b in blocks if b.block_id not in {"macro", "event_information"}) / max(
        len([b for b in blocks if b.block_id not in {"macro", "event_information"}]), 1
    )
    confidence = max(0, confidence - 15 * len(conflicts))
    return Decision(status, 250, round(confidence, 1), fraction, tuple(conflicts))
