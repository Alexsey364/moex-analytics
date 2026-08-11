from moex_analytics.portfolio_review.core import AMOUNTS


def test_required_new_money_scenarios_are_complete() -> None:
    assert AMOUNTS == (50_000.0, 100_000.0, 250_000.0, 500_000.0)
