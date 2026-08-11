from moex_analytics.evidence_registry.core import BLOCKS, evidence_strength


def test_evidence_strength_requires_positive_ci_stable_folds_and_history() -> None:
    strong = evidence_strength(gain=.02, ci_low=.01, folds=True, sample_n=100, fresh=True)
    weak = evidence_strength(gain=.02, ci_low=-.01, folds=True, sample_n=100, fresh=True)
    assert strong[0] == "STRONG_RESEARCH_EVIDENCE"
    assert weak[0] == "WEAK_RESEARCH_EVIDENCE"
    assert evidence_strength(gain=.02, ci_low=.01, folds=False, sample_n=100, fresh=True)[0] == "UNSTABLE"
    short = evidence_strength(gain=.02, ci_low=.01, folds=True, sample_n=20, fresh=True)
    assert short[0] == "INSUFFICIENT_HISTORY"


def test_registry_contract_has_all_required_blocks() -> None:
    expected = {
        "baseline", "market_conditioned", "sector_conditioned", "ranking", "distribution",
        "analog", "news", "fundamental", "valuation", "risk", "portfolio_concentration", "live",
    }
    assert expected == set(BLOCKS)
