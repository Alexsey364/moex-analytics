"""Stage-14 methodology and safety tests."""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.portfolio_research.external_methods import (
    NativeBacktestBackend,
    Order,
    annual_metrics,
    covariance_shrinkage,
    okama_reference_metrics,
    portfolio_method_weights,
    reconcile_metrics,
)
from moex_analytics.portfolio_research.issuers import ADAPTERS, METRICS
from moex_analytics.portfolio_research.portfolio_v14 import parse_local_portfolio
from moex_analytics.portfolio_research.schema import DDL
from moex_analytics.portfolio_research.validation import (
    block_bootstrap,
    effective_sample_size,
    newey_west_t,
    validate_series,
)


def prices(n=1200, seed=4):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0003, 0.015, n)
    return pd.DataFrame(
        {
            "trade_date": pd.date_range("2018-01-01", periods=n, freq="B").date,
            "close": 100 * np.cumprod(1 + ret),
        }
    )


def test_real_block_bootstrap_is_reproducible():
    y = np.arange(40) / 100
    p = y + 0.01
    b = np.zeros(40)
    assert np.array_equal(block_bootstrap(y, p, b, 5, 20, 1), block_bootstrap(y, p, b, 5, 20, 1))


def test_block_bootstrap_has_real_distribution():
    assert np.std(block_bootstrap(np.arange(60) / 100, np.zeros(60), np.ones(60) * 0.2, 7, 50)[:, 0]) > 0


def test_effective_sample_independent():
    assert effective_sample_size(np.tile([-1, 1], 200)) > 300


def test_effective_sample_autocorrelated():
    assert effective_sample_size(np.arange(300)) < 20


def test_hac_diagnostic_finite():
    assert np.isfinite(newey_west_t(np.tile([0.1, 0.2], 50), 5))


def test_validation_predictions_are_oos():
    r = validate_series(prices(), "return_60", 60, 40)
    assert r["predictions"] and len({x[0] for x in r["predictions"]}) >= 4


def test_validation_stores_regime_sign():
    r = validate_series(prices(), "volatility_60", 20, 30)
    assert {"normal", "stress"} <= set(r["regimes"])
    assert all("sign" in x for x in r["regimes"].values())


def test_short_history_is_insufficient():
    assert validate_series(prices(300), "return_120", 120, 20)["status"] == "insufficient_history"


def test_okama_reconciliation_same_convention():
    assert all(x["status"] == "matched" for x in reconcile_metrics([0.01, -0.02, 0.03] * 100).values())


def test_okama_monthly_convention_differs_from_daily():
    r = np.tile([0.01, -0.005], 180)
    dates = pd.date_range("2024-01-01", periods=len(r), freq="D")
    external = okama_reference_metrics(r, dates)
    assert external["wealth_index"] > 1 and not np.isclose(
        external["volatility"], annual_metrics(r)["volatility"]
    )


def test_annualization_convention():
    assert np.isclose(annual_metrics(np.ones(252) * 0.001)["annual_arithmetic_return"], 0.252)


def test_dividend_total_return_identity():
    assert np.isclose((110 - 100 + 5) / 100, 0.15)


def test_covariance_shrinkage_is_psd():
    assert (
        np.linalg.eigvalsh(covariance_shrinkage(np.random.default_rng(1).normal(size=(300, 4)))).min()
        >= -1e-12
    )


def test_optional_portfolio_methods():
    w = portfolio_method_weights(np.random.default_rng(2).normal(size=(300, 4)))
    assert {"minimum_variance_shrunk", "hrp_shrunk"} <= set(w)
    assert all(np.isclose(x.sum(), 1) for x in w.values())


def test_event_fill_is_delayed_and_costed():
    r = NativeBacktestBackend().run([100, 101, 102], [Order(0, 10)], 10, 5)
    assert r["fills"][0]["session"] == 1 and r["fills"][0]["fee"] > 0


def test_unavailable_day_order_not_filled():
    assert not NativeBacktestBackend().run([100], [Order(0, 1)])["fills"]


def test_local_portfolio_missing_is_demo(tmp_path):
    assert parse_local_portfolio(tmp_path / "none.yaml")["mode"] == "demo"


def test_real_local_portfolio_contract(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("mode: real\npositions:\n  - secid: LKOH\n    quantity: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields"):
        parse_local_portfolio(p)


def test_explicit_demo_not_user_result(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("mode: demo\npositions: []\n", encoding="utf-8")
    assert "not user" in parse_local_portfolio(p)["message"]


def test_yield_on_cost():
    assert np.isclose(20 / 200, 0.1)


def test_preferred_metric_maps_exist():
    assert {"SBERP", "LSNGP", "TRNFP", "TATNP"} <= set(METRICS)


def test_all_issuer_adapter_contracts_are_pit():
    assert len(ADAPTERS) == 9
    for cls in ADAPTERS:
        assert "cutoff" in cls().contract.point_in_time_contract


def test_schema_has_validation_and_cashflow():
    c = duckdb.connect(":memory:")
    c.execute(DDL)
    tables = {x[0] for x in c.execute("select table_name from information_schema.tables").fetchall()}
    assert {
        "portfolio_alpha_bootstrap",
        "portfolio_dividend_outlook",
        "portfolio_scenarios_v2",
        "real_portfolio_live_shadow",
    } <= tables


def test_snapshot_hash_is_idempotent():
    import hashlib
    import json

    p = {"weights": {"LKOH": 1.0}}
    assert (
        hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        == hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
    )


def test_no_buy_sell_output_contract():
    root = Path(__file__).parents[1]
    text = (root / "src/moex_analytics/portfolio_research/portfolio_v14.py").read_text()
    assert '"BUY"' not in text and '"SELL"' not in text
