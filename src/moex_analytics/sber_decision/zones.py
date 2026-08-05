"""Dynamic, rounded SBER price zones."""


def _round5(value: float) -> float:
    return round(value / 5) * 5


def build_zones(main_low: float, main_high: float, stress_low: float, stress_high: float) -> list[dict]:
    stress = _round5(max(stress_low, main_low * 0.70))
    low = _round5(main_low)
    middle = _round5((main_low + main_high) / 2)
    high = _round5(main_high)
    upper = max(high, _round5(stress_high))
    names = [
        ("стрессовый уровень", None, stress, "сначала пересмотреть риски", 0.0),
        ("сильная зона накопления", stress, low, "поэтапно накапливать", 0.50),
        ("умеренная зона покупки", low, middle, "небольшая первая часть", 0.25),
        ("нейтральная зона", middle, high, "наблюдать", 0.10),
        ("зона, где не следует догонять цену", high, upper, "не догонять цену", 0.0),
        ("зона фундаментальной переоценки", upper, None, "не наращивать", 0.0),
    ]
    return [{"name": n, "low": lo, "high": hi, "action": a, "max_fraction": f} for n, lo, hi, a, f in names]


def staged_plan(planned: float, max_portfolio_fraction: float, zones: list[dict]) -> dict:
    cap = max(0, min(planned, planned * max_portfolio_fraction))
    return {
        "first": cap * 0.25,
        "second": cap * 0.35,
        "after_report": cap * 0.20,
        "reserve": cap * 0.20,
        "stop": "ухудшение капитала, ROE, качества активов или дивидендной политики",
        "zones": zones,
    }
