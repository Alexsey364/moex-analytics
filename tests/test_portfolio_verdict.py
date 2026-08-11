from moex_analytics.portfolio_verdict.core import action_policy


def test_red_requires_concentration_not_weak_model_output() -> None:
    action, _ = action_policy(
        stress=False, concentration=0.3, positive=0, negative=0, eligible_direction=False
    )
    assert action.startswith("🔴")
    weak, _ = action_policy(stress=False, concentration=0.1, positive=0, negative=1, eligible_direction=False)
    assert not weak.startswith("🔴")


def test_positive_action_requires_eligible_directional_evidence() -> None:
    action, _ = action_policy(
        stress=False, concentration=0.1, positive=2, negative=0, eligible_direction=True
    )
    assert action.startswith("🟢")
    no_direction, _ = action_policy(
        stress=False, concentration=0.1, positive=2, negative=0, eligible_direction=False
    )
    assert no_direction.startswith("🟡")
