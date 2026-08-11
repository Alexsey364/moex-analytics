import pandas as pd

from moex_analytics.dashboard.pages.evidence_decision import (
    _table,
    answer_question,
    evidence_badge,
    human_direction,
)


def test_evidence_badges_are_not_confidence_percentages() -> None:
    assert evidence_badge("low") == "● слабая"
    assert evidence_badge("medium") == "●● средняя"
    assert evidence_badge("stronger") == "●●● повышенная"
    assert "%" not in evidence_badge("stronger")


def test_direction_vocabulary_does_not_leak_research_status() -> None:
    assert human_direction("positive") == "положительно"
    assert human_direction("unknown") == "недостаточно данных"


def _payload() -> dict:
    horizons = []
    for instrument, base_rank in (("MTSS", 1), ("SBERP", 2), ("MOEX", 3)):
        for horizon in (5, 20, 60, 120, 250):
            horizons.append(
                {
                    "instrument": instrument,
                    "horizon": horizon,
                    "directional_state": "positive" if instrument == "MTSS" else "neutral",
                    "evidence_strength": "medium" if horizon == 120 else "low",
                    "relative_group": "выше средней",
                    "relative_rank": base_rank,
                }
            )
    verdicts = pd.DataFrame(
        [
            {"instrument": "MTSS", "risk_status": "high", "portfolio_action": "🟡 observe"},
            {"instrument": "SBERP", "risk_status": "high", "portfolio_action": "🔴 concentration"},
            {"instrument": "MOEX", "risk_status": "high", "portfolio_action": "🟡 observe"},
        ]
    )
    return {
        "cutoff": "2026-08-10",
        "horizons": pd.DataFrame(horizons),
        "verdicts": verdicts,
        "allocations": pd.DataFrame(
            [{"amount": 100_000, "cash_reserve": 100_000, "reason": "CASH_PREFERRED"}]
        ),
    }


def test_table_and_qa_use_the_same_saved_payload() -> None:
    payload = _payload()
    table = _table(payload)
    assert set(table["Акция"]) == {"MTSS", "SBERP", "MOEX"}
    assert "●● средняя" in table.loc[table["Акция"] == "MTSS", "6 месяцев"].iloc[0]
    questions = (
        "Что сейчас лучше выглядит?",
        "MTSS status?",
        "Почему SBERP не зелёный?",
        "Почему оставить 100 тысяч в резерве?",
        "Какая бумага сильнее на 6 месяцев?",
        "Где основной риск?",
    )
    answers = [answer_question(payload, question) for question in questions]
    assert all(answers)
    assert "100,000" in answers[3]
    assert "MTSS" in answers[4]
    assert "SBERP" in answers[5]
