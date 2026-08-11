import numpy as np

from moex_analytics.whole_market_tournament.core import bh_adjust, paired_evidence


def test_bootstrap_and_permutation_are_deterministic() -> None:
    deltas = np.linspace(0.01, 0.05, 100)
    first = paired_evidence(deltas, 200)
    assert first == paired_evidence(deltas, 200)
    assert first[0] > 0
    assert first[2] < 0.05


def test_bh_adjustment_is_monotone_and_preserves_missing() -> None:
    adjusted = bh_adjust([0.01, 0.04, None, 0.02])
    assert adjusted[2] is None
    assert adjusted[0] <= adjusted[3] <= adjusted[1]
