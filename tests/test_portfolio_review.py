from moex_analytics.portfolio_review.core import AMOUNTS, _render


def test_required_new_money_scenarios_are_complete() -> None:
    assert AMOUNTS == (50_000.0, 100_000.0, 250_000.0, 500_000.0)


def test_review_render_keeps_human_and_technical_layers() -> None:
    verdicts = [("MTSS", "research_only", "🟡 observe", "high", "mixed", "[]", "[]")]
    horizons = [
        (
            "MTSS",
            horizon,
            "neutral",
            "medium",
            "выше средней",
            1,
            "stress",
            "sector",
            "historically lower MAE",
            "shadow",
            "context",
            "high",
            "weight 10%",
            "too small",
            False,
        )
        for horizon in (5, 20, 60, 120, 250)
    ]
    plans = [
        ["run", amount, '{"CASH": 100000}', amount, "CASH_PREFERRED", "not forced"] for amount in AMOUNTS
    ]
    report = _render("2026-08-10", verdicts, horizons, plans, "hash")
    assert "Human comparison" in report
    assert "Technical evidence" in report
    assert "CASH_PREFERRED" in report
    assert "Production changes: 0" in report
