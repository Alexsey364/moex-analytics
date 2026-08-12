import numpy as np
import pandas as pd

from moex_analytics.macro_sensitivity import estimate_sensitivity


def test_sensitivity_recovers_sign_and_stability() -> None:
    factor = pd.Series(np.linspace(-1, 1, 200))
    beta, stability = estimate_sensitivity(2 * factor, factor)
    assert abs(beta - 2) < 1e-9 and stability == 1


def test_short_or_constant_factor_is_insufficient() -> None:
    assert estimate_sensitivity(pd.Series(range(20)), pd.Series(range(20)))[0] is None
    assert estimate_sensitivity(pd.Series(range(100)), pd.Series(np.ones(100)))[0] is None
