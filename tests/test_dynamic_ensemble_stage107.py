from moex_analytics.dynamic_ensemble import component_weight


def test_only_validated_gets_normal_weight() -> None:
    assert component_weight("VALIDATED") == 1
    assert 0 < component_weight("WEAK") < 1
    assert component_weight("FAILED") == 0
    assert component_weight("INSUFFICIENT_DATA") == 0
