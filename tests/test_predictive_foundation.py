from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import pytest

from moex_analytics.predictive_foundation.core import (
    SOURCE_MATRIX,
    align_publication,
    audit_coverage,
    breadth_frame,
    build_breadth,
    build_catalog,
    build_feature_families,
    build_lead_lag_diagnostics,
    build_relative_state,
    choose_front_contract,
    common_sample_ablation,
    cross_market_status,
    derivative_features_status,
    detect_structural_regimes,
    discover_derivatives,
    discover_market_universe,
    ensure_schema,
    futures_basis,
    implied_volatility,
    index_history_status,
    index_members_as_of,
    ingest_history,
    lead_lag,
    option_arbitrage_valid,
    rates_market_status,
    select_liquid_universe,
    split_session,
    status,
    update,
    yield_curve_features,
)


class FakeClient:
    base_url = "https://iss.moex.com/iss"

    def get_json(self, path, params=None):
        del params
        if path.startswith("engines/stock"):
            return {
                "securities": {
                    "columns": ["SECID", "BOARDID", "STATUS", "ISSUESIZEPLACEDATE"],
                    "data": [["AAA", "TQBR", "A", "2010-01-01"], ["OLD", "TQBR", "D", "2000-01-01"]],
                },
                "marketdata": {
                    "columns": ["SECID", "VALTODAY", "ISSUECAPITALIZATION"],
                    "data": [["AAA", 1000.0, 5000.0], ["OLD", 0.0, None]],
                },
            }
        return {
            "securities": {
                "columns": ["SECID", "SHORTNAME", "ASSETCODE", "LASTTRADEDATE", "BOARDID", "IS_TRADED"],
                "data": [
                    ["SRU6", "SBER-9.26", "SBER", "2026-09-17", "RFUD", 1],
                    ["OTHER", "Other", "OTHER", "2026-09-17", "RFUD", 1],
                ],
            }
        }

    def history_pages(self, instrument, date_from, date_to):
        del date_from, date_to
        payload = {
            "history": {
                "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE", "NUMTRADES"],
                "data": [["2020-01-01", 10, 11, 9, 10.5, 100, 1050, 20]],
            }
        }
        yield payload, 0, f"https://iss.moex.com/{instrument['source_secid']}"


def memory():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    con.execute(
        """create table canonical_daily_prices(
        trade_date DATE,canonical_secid VARCHAR,close DOUBLE)"""
    )
    con.execute(
        """create table macro_observations(
        series_id VARCHAR,observation_date DATE,value DOUBLE)"""
    )
    return con


def prices_frame():
    rows = []
    start = date(2020, 1, 1)
    for index in range(260):
        for secid, multiplier in (("AAA", 1.0), ("BBB", 1.2), ("CCC", 0.8)):
            rows.append(
                {
                    "trade_date": start + timedelta(days=index),
                    "secid": secid,
                    "close": (100 + index * 0.1 + np.sin(index / 5)) * multiplier,
                    "value": 1000 * multiplier,
                }
            )
    return pd.DataFrame(rows)


def test_catalog_is_complete_matrix_and_paid_fallback_explicit():
    con = memory()
    result = build_catalog(con)
    assert result["datasets"] == len(SOURCE_MATRIX)
    assert result["paid_or_blocked"] > 0
    paid = con.execute(
        "select count(*) from predictive_data_catalog where model_eligibility='requires_paid_source'"
    ).fetchone()[0]
    assert paid == result["paid_or_blocked"]
    assert (
        con.execute(
            "select count(*) from predictive_data_catalog where source ilike '%aggregator%'"
        ).fetchone()[0]
        == 0
    )


def test_historical_universe_survivorship_and_boards():
    con = memory()
    result = discover_market_universe(con, FakeClient())
    assert result["instruments"] == 2
    active, inactive = con.execute(
        "select sum(is_traded::int),sum((not is_traded)::int) from predictive_market_universe"
    ).fetchone()
    assert active == inactive == 1
    assert (
        con.execute(
            "select survivorship_status from predictive_market_universe where source_secid='OLD'"
        ).fetchone()[0]
        == "current_universe_only"
    )
    assert select_liquid_universe(con, 1) == ["AAA"]


def test_history_is_official_and_available_after_close():
    con = memory()
    discover_market_universe(con, FakeClient())
    result = ingest_history(con, ["AAA"], FakeClient(), "2020-01-01", "2020-01-02")
    assert result["rows_written"] == 1
    row = con.execute("select board,source,available_from from predictive_market_prices").fetchone()
    assert row[0] == "TQBR"
    assert row[1].startswith("https://iss.moex.com/")
    assert row[2].astimezone(ZoneInfo("Europe/Moscow")).hour == 19


def test_breadth_equal_weight_and_no_future_data():
    frame = prices_frame()
    original = breadth_frame(frame)
    changed = frame.copy()
    changed.loc[changed.trade_date > date(2020, 7, 1), "close"] *= 10
    modified = breadth_frame(changed)
    cutoff = date(2020, 6, 1)
    left = original[original.trade_date <= cutoff].reset_index(drop=True)
    right = modified[modified.trade_date <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
    day = original.dropna(subset=["equal_weight_return"]).iloc[-1]
    raw = frame[frame.trade_date == day.trade_date]
    previous = frame[frame.trade_date == day.trade_date - timedelta(days=1)]
    expected = np.mean(
        [
            raw[raw.secid == secid].close.iloc[0] / previous[previous.secid == secid].close.iloc[0] - 1
            for secid in ("AAA", "BBB", "CCC")
        ]
    )
    assert day.equal_weight_return == pytest.approx(expected)


def test_build_breadth_and_coverage():
    con = memory()
    frame = prices_frame()
    con.register("incoming", frame)
    con.execute(
        """insert into predictive_market_prices
        select trade_date,secid,'TQBR',close,close,close,close,1,value,1,
        cast(trade_date as timestamp)+interval 19 hour,'official' from incoming"""
    )
    assert build_breadth(con)["maximum_universe"] == 3
    assert audit_coverage(con)["series"] == 3
    assert status(con)["breadth"] > 0


def test_futures_roll_basis_and_open_interest_fields():
    basis, annual = futures_basis(105, 100, 30)
    assert basis == pytest.approx(0.05)
    assert annual == pytest.approx(0.05 * 365 / 30)
    with pytest.raises(ValueError):
        futures_basis(105, 0, 30)
    con = memory()
    result = discover_derivatives(con, FakeClient())
    assert result["discovered"] == 1
    row = con.execute(
        "select secid,asset_code,model_eligibility from predictive_derivative_instruments"
    ).fetchone()
    assert row == ("SRU6", "SBER", "experimental")


@pytest.mark.parametrize(
    ("price", "spot", "strike", "kind", "valid"),
    [
        (10, 100, 100, "call", True),
        (101, 100, 100, "call", False),
        (1, 100, 120, "put", False),
        (25, 100, 120, "put", True),
    ],
)
def test_options_arbitrage_bounds(price, spot, strike, kind, valid):
    assert option_arbitrage_valid(price, spot, strike, kind) is valid


def test_implied_volatility_and_bad_quote_rejected():
    volatility = implied_volatility(10.45, 100, 100, 1, 0.05)
    assert volatility == pytest.approx(0.20, abs=0.02)
    assert implied_volatility(200, 100, 100, 1, 0.05) is None


def test_yield_curve_level_slope_curvature():
    result = yield_curve_features([1, 5, 10], [10, 11, 12])
    assert result["slope"] == 2
    assert result["curvature"] == 0
    assert not result["inverted"]


def test_publication_cutoff_timezone_and_session_split():
    published = datetime(2024, 1, 2, 12, tzinfo=UTC)
    assert not align_publication(date(2024, 1, 1), published, datetime(2024, 1, 2, 11, tzinfo=UTC))
    assert align_publication(date(2024, 1, 1), published, datetime(2024, 1, 2, 13, tzinfo=UTC))
    assert split_session(datetime(2024, 1, 2, 7, tzinfo=UTC)) == "main"
    assert split_session(datetime(2024, 1, 2, 17, tzinfo=UTC)) == "evening"


def test_frequency_separation_and_feature_families():
    con = memory()
    build_catalog(con)
    assert (
        con.execute(
            """select count(distinct frequency) from predictive_data_catalog
        where frequency in ('daily','intraday','tick','monthly')"""
        ).fetchone()[0]
        >= 4
    )
    assert build_feature_families(con)["families"] >= 10


def test_common_sample_ablation_and_lead_lag_are_diagnostic():
    index = pd.date_range("2020-01-01", periods=100)
    base = pd.DataFrame({"base": np.arange(100)}, index=index)
    target = pd.Series(np.sin(np.arange(100) / 5), index=index)
    blocks = {
        "breadth": pd.DataFrame({"signal": target.shift(-1)}, index=index),
        "rates": pd.DataFrame({"signal": -target}, index=index),
    }
    results = common_sample_ablation(base, blocks, target, horizons=(1, 5))
    assert len(results) == 4
    assert len({row["common_sample"] for row in results}) <= 2
    correlations = lead_lag(target.shift(1), target, max_lag=2)
    assert set(correlations) == {-2, -1, 0, 1, 2}


def test_relative_regime_wrappers_ablation_and_full_update():
    con = memory()
    start = date(2019, 1, 1)
    canonical = []
    for index in range(400):
        day = start + timedelta(days=index)
        canonical.extend(
            [
                (day, "SBER", 200 + index * 0.2 + np.sin(index / 8)),
                (day, "IMOEX", 2000 + index + 10 * np.sin(index / 10)),
                (day, "MOEXFN", 1000 + index * 0.5 + 5 * np.sin(index / 9)),
            ]
        )
    con.executemany("insert into canonical_daily_prices values (?,?,?)", canonical)
    con.execute("insert into macro_observations values ('cbr_key_rate','2020-01-01',6)")
    assert build_relative_state(con)["rows"] == 400
    assert detect_structural_regimes(con)["rows"] > 0
    assert index_history_status(con)["official_index_series"]
    assert rates_market_status(con)["existing_official_series"]
    assert cross_market_status(con)["series"]
    assert derivative_features_status(con)["synthetic_roll"] is False

    market = prices_frame()
    con.register("market_input", market)
    con.execute(
        """insert into predictive_market_prices
        select trade_date,secid,'TQBR',close,close,close,close,1,value,1,
        cast(trade_date as timestamp)+interval 19 hour,'official' from market_input"""
    )
    build_breadth(con)
    build_relative_state(con)
    ablation = __import__(
        "moex_analytics.predictive_foundation.core", fromlist=["ablate_blocks"]
    ).ablate_blocks(con)
    assert ablation["rows"] == 12
    assert build_lead_lag_diagnostics(con)["causality_claimed"] is False

    fresh = memory()
    fresh.executemany("insert into canonical_daily_prices values (?,?,?)", canonical)
    result = update(fresh, FakeClient(), universe_limit=1)
    assert result["catalog"]["datasets"] == len(SOURCE_MATRIX)
    assert result["selected"] == 1
    assert result["history"]["rows_written"] == 1


def test_index_membership_point_in_time_and_futures_roll():
    membership = pd.DataFrame(
        {
            "constituent": ["AAA", "BBB"],
            "effective_from": ["2020-01-01", "2021-01-01"],
            "effective_to": ["2020-12-31", None],
            "available_from": ["2019-12-20", "2020-12-20"],
        }
    )
    assert index_members_as_of(membership, date(2020, 6, 1)).constituent.tolist() == ["AAA"]
    contracts = pd.DataFrame(
        {
            "secid": ["front", "next"],
            "expiration_date": ["2024-03-15", "2024-06-15"],
            "is_traded": [True, True],
        }
    )
    assert choose_front_contract(contracts, date(2024, 3, 1)) == "front"
    assert choose_front_contract(contracts, date(2024, 3, 12), roll_days=5) == "next"


def test_catalog_no_change_rerun():
    con = memory()
    first = build_catalog(con)
    second = build_catalog(con)
    assert first == second
    assert con.execute("select count(*) from predictive_data_catalog").fetchone()[0] == len(SOURCE_MATRIX)
