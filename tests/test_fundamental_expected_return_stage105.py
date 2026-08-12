from __future__ import annotations

from moex_analytics.fundamental_expected_return.core import decompose


def test_decomposition_is_additive_capped_and_horizon_scaled() -> None:
    short = decompose(.08, .50, 3.0, 120)
    long = decompose(.08, .50, 3.0, 250)
    assert long["earnings"] == .20
    assert long["rerating"] == .05
    assert long["total"] == long["dividend"] + long["earnings"] + long["rerating"]
    assert short["total"] < long["total"]


def test_no_fundamental_evidence_stays_unavailable() -> None:
    result = decompose(None, None, None, 250)
    assert result == {"dividend": None, "earnings": None, "rerating": None, "total": None}


def test_dimensionless_scores_are_not_implicitly_interpreted_by_caller() -> None:
    dividend_only = decompose(.08, None, None, 250)
    assert dividend_only["total"] == .08
    assert dividend_only["earnings"] == 0
