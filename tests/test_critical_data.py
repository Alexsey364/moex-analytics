import math
from datetime import date, datetime

import duckdb
import pandas as pd
import pytest

from moex_analytics.critical_data.core import (
    MOSCOW,
    back_adjust,
    basis,
    classify_session,
    common_sample_ablation,
    ensure_schema,
    futures_roll,
    historical_breadth,
    interpolate_curve,
    option_arbitrage_valid,
    overnight_gap,
    parse_zcyc_html,
    parse_zcyc_point_html,
    point_in_time_membership,
    publication_available,
    split_sessions,
    status,
    survivorship_impact,
)


def life(secid="OLD", board="TQBR", end=None, primary=True):
    return {
        "secid": secid,
        "board": board,
        "history_from": "2020-01-01",
        "history_to": end,
        "is_primary": primary,
    }


def test_schema_and_no_change():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    first = status(con)
    ensure_schema(con)
    assert status(con) == first


def test_point_in_time_delisted_and_past_liquidity():
    prices = pd.DataFrame(
        [
            {"trade_date": "2020-01-01", "secid": "OLD", "board": "TQBR", "close": 10, "value": 1},
            {"trade_date": "2020-01-02", "secid": "OLD", "board": "TQBR", "close": 11, "value": 1000},
            {"trade_date": "2020-01-03", "secid": "OLD", "board": "TQBR", "close": 12, "value": 1000},
        ]
    )
    result = point_in_time_membership(
        prices, pd.DataFrame([life(end="2020-01-02")]), min_liquidity=500, lookback=1
    )
    assert not result.iloc[1].eligible  # today's spike cannot enter today
    assert not result.iloc[2].eligible  # delisted, even though prior liquidity qualifies


def test_board_overlap_deduplicated():
    prices = pd.DataFrame(
        [
            {"trade_date": "2020-01-02", "secid": "SBER", "board": "TQBR", "close": 10, "value": 10},
            {"trade_date": "2020-01-02", "secid": "SBER", "board": "TQTD", "close": 10, "value": 100},
        ]
    )
    lives = pd.DataFrame([life("SBER", "TQBR", primary=True), life("SBER", "TQTD", primary=False)])
    result = point_in_time_membership(prices, lives)
    assert len(result) == 1 and result.iloc[0].board == "TQBR"


def test_historical_breadth_and_survivorship():
    prices = pd.DataFrame(
        [
            {"trade_date": date(2020, 1, 1), "secid": "A", "close": 10},
            {"trade_date": date(2020, 1, 2), "secid": "A", "close": 11},
            {"trade_date": date(2020, 1, 1), "secid": "OLD", "close": 10},
            {"trade_date": date(2020, 1, 2), "secid": "OLD", "close": 9},
        ]
    )
    membership = pd.DataFrame(
        [{"trade_date": r.trade_date, "secid": r.secid, "eligible": True} for r in prices.itertuples()]
    )
    breadth = historical_breadth(prices, membership)
    assert breadth.iloc[-1].universe_size == 2
    returns = prices.sort_values(["secid", "trade_date"])
    returns["return"] = returns.groupby("secid").close.pct_change()
    effect = survivorship_impact(returns, ["A"])
    assert effect["mean_daily_bias"] > 0


def test_zcyc_parsing_interpolation_publication():
    cells = "".join(
        f"<td>{v}</td>"
        for v in [
            "01.02.2024",
            "10,0",
            "10,1",
            "10,2",
            "10,3",
            "10,4",
            "10,5",
            "10,6",
            "10,7",
            "10,8",
            "10,9",
        ]
    )
    parsed = parse_zcyc_html(f"<table><tr>{cells}</tr></table>")
    assert len(parsed) == 10 and parsed[0][2] == 10
    points = parse_zcyc_point_html(
        "<table><tr><td>0.25 1 2</td></tr><tr><td>10 11 12</td></tr></table>", date(2024, 2, 1)
    )
    assert points[-1][1:] == (2.0, 12.0)
    curve = interpolate_curve([(1, 10), (2, 12)], (1, 1.5, 2))
    assert curve[1.5] == 11
    assert not publication_available(
        date(2024, 2, 1), date(2024, 2, 1), datetime(2024, 2, 1, 18, tzinfo=MOSCOW)
    )
    assert publication_available(date(2024, 2, 1), date(2024, 2, 1), datetime(2024, 2, 1, 20, tzinfo=MOSCOW))
    with pytest.raises(ValueError):
        interpolate_curve([(1, 200)])


def test_futures_roll_adjustment_basis_and_oi():
    old = pd.DataFrame(
        [
            {
                "trade_date": "2024-01-01",
                "secid": "SRH4",
                "close": 100,
                "volume": 10,
                "open_interest": 20,
                "expiration": "2024-01-10",
            }
        ]
    )
    new = pd.DataFrame(
        [
            {
                "trade_date": "2024-01-01",
                "secid": "SRM4",
                "close": 105,
                "volume": 30,
                "open_interest": 40,
                "expiration": "2024-03-10",
            }
        ]
    )
    roll = futures_roll(old, new, "liquidity")
    assert roll["new_contract"] == "SRM4" and roll["new_oi"] == 40
    adjusted = back_adjust(
        pd.DataFrame(
            [{"trade_date": "2023-12-31", "close": 100}, {"trade_date": "2024-01-01", "close": 105}]
        ),
        [roll],
    )
    assert adjusted.iloc[0].back_adjusted_close == 105
    raw, annual = basis(105, 100, 30)
    assert raw == pytest.approx(0.05) and annual == pytest.approx(0.05 * 365 / 30)
    assert math.isnan(basis(1, 1, 0)[1])


def test_options_bounds():
    assert option_arbitrage_valid(12, 100, 90, "call")
    assert not option_arbitrage_valid(5, 100, 90, "call")
    assert option_arbitrage_valid(12, 90, 100, "put")


def test_intraday_sessions_and_gap():
    assert classify_session("2024-01-01 09:00") == "morning"
    assert classify_session("2024-01-01 12:00") == "main"
    assert classify_session("2024-01-01 20:00") == "evening"
    split = split_sessions(pd.DataFrame({"begin": ["2024-01-01 09:00", "2024-01-01 20:00"]}))
    assert split.session.tolist() == ["morning", "evening"]
    assert overnight_gap(100, 105) == pytest.approx(0.05)


def test_common_sample_and_no_future_rows():
    base = pd.Series([1, 2, 3, 4], index=[1, 2, 3, 4])
    block = pd.Series([1, None, 2, 5], index=[1, 2, 3, 4])
    target = pd.Series([1, 3, 2, 5], index=[1, 2, 3, 4])
    result = common_sample_ablation(base, block, target)
    assert result["n"] == 3
    same = common_sample_ablation(base, base, target)
    assert same["n"] == 4
