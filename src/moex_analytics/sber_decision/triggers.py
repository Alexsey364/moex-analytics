"""Rule thresholds which can change the current decision."""


def build(price: float, main_low: float, eps: float, roe: float, dividend: float) -> list[dict]:
    return [
        {
            "id": "price_main",
            "category": "market",
            "condition": f"цена снизится до {main_low:.0f} RUB",
            "change": "пересчитать допустимую первую долю",
            "value": main_low,
            "unit": "RUB",
        },
        {
            "id": "eps_down",
            "category": "fundamental",
            "condition": f"EPS станет ниже {eps * 0.85:.1f}",
            "change": "пересмотреть фундаментальные предпосылки",
            "value": eps * 0.85,
            "unit": "RUB/share",
        },
        {
            "id": "roe_down",
            "category": "fundamental",
            "condition": f"ROE станет ниже {roe * 0.85:.1%}",
            "change": "не наращивать",
            "value": roe * 0.85,
            "unit": "ratio",
        },
        {
            "id": "dividend",
            "category": "fundamental",
            "condition": f"официальный DPS будет ниже {dividend * 0.85:.1f}",
            "change": "снизить дивидендную привлекательность",
            "value": dividend * 0.85,
            "unit": "RUB/share",
        },
        {
            "id": "new_report",
            "category": "information",
            "condition": "появится новый validated отчёт",
            "change": "пересчитать все фундаментальные блоки",
            "value": None,
            "unit": "event",
        },
    ]
