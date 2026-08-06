from datetime import date

import duckdb
import pandas as pd

from moex_analytics.deep_backfill.core import (
    CURVE_TENORS,
    classify_multi_session,
    contract_multiplier_valid,
    coverage_suitability,
    derive_rolls,
    distinguish_option_history,
    dynamic_liquidity_selection,
    effective_sample_size,
    ensure_schema,
    infer_quarterly_expiration,
    parse_zcyc_archive,
    ratio_adjust,
    select_continuous_contracts,
    survivorship_comparison,
    validate_curve_dates,
    validate_ifrs_review,
)


def test_schema_no_change():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    ensure_schema(con)
    assert con.execute("select count(*) from sber_predictive_common_sample").fetchone()[0] == 0


def test_historical_zcyc_archive_multiple_dates():
    header = "<tr><th>Дата</th>" + "".join(f"<th>{x}</th>" for x in CURVE_TENORS) + "</tr>"
    rows = "".join(
        "<tr><td>0{}.01.2020</td>{}</tr>".format(
            day, "".join(f"<td>{10 + day / 10 + i / 100:.2f}</td>" for i in range(12))
        )
        for day in (1, 2)
    )
    parsed = parse_zcyc_archive(f"<table>{header}{rows}</table>")
    assert len(parsed) == 24 and validate_curve_dates(parsed) == {
        "dates": 2,
        "valid_dates": 2,
        "invalid_dates": 0,
    }


def test_invalid_curve_units_rejected():
    values = "".join("<td>200</td>" for _ in range(12))
    assert parse_zcyc_archive(f"<tr><td>01.01.2020</td>{values}</tr>") == []


def test_expired_futures_multiplier_and_roll_history():
    assert contract_multiplier_valid(100, 1, 100)
    assert not contract_multiplier_valid(None, 1, 100)
    frame = pd.DataFrame(
        [
            {
                "trade_date": "2024-01-01",
                "secid": "SRH4",
                "expiration": "2024-01-03",
                "close": 100,
                "settlement": 100,
                "volume": 100,
                "open_interest": 100,
                "multiplier": 100,
                "price_scale": 1,
                "underlying_units": 100,
            },
            {
                "trade_date": "2024-01-01",
                "secid": "SRM4",
                "expiration": "2024-03-20",
                "close": 102,
                "settlement": 102,
                "volume": 50,
                "open_interest": 50,
                "multiplier": 100,
                "price_scale": 1,
                "underlying_units": 100,
            },
            {
                "trade_date": "2024-01-02",
                "secid": "SRH4",
                "expiration": "2024-01-03",
                "close": 101,
                "settlement": 101,
                "volume": 20,
                "open_interest": 20,
                "multiplier": 100,
                "price_scale": 1,
                "underlying_units": 100,
            },
            {
                "trade_date": "2024-01-02",
                "secid": "SRM4",
                "expiration": "2024-03-20",
                "close": 104,
                "settlement": 104,
                "volume": 200,
                "open_interest": 200,
                "multiplier": 100,
                "price_scale": 1,
                "underlying_units": 100,
            },
        ]
    )
    selected = select_continuous_contracts(frame, "combined", days_before=1)
    rolls = derive_rolls(selected, "combined")
    assert selected.secid.tolist() == ["SRH4", "SRM4"] and len(rolls) == 1 and rolls[0]["pit_safe"]
    adjusted = ratio_adjust(selected, rolls)
    assert adjusted.iloc[0].ratio_adjusted_close > 100


def test_dynamic_universe_uses_past_liquidity_and_delisted():
    rows = []
    for secid, values in {"LIVE": [10, 10, 10, 10], "OLD": [5, 5, 5, 5]}.items():
        for i, value in enumerate(values):
            rows.append(
                {
                    "trade_date": date(2020, 1, 1 + i),
                    "secid": secid,
                    "board": "TQBR",
                    "close": 10 + i,
                    "value": value if i < 3 else 10000,
                }
            )
    prices = pd.DataFrame(rows)
    selected = dynamic_liquidity_selection(prices, min_history=2, max_size=2)
    old = selected[(selected.secid == "OLD") & selected.eligible]
    assert not old.empty
    assert (
        selected[(selected.trade_date == pd.Timestamp("2020-01-04")) & (selected.secid == "OLD")]
        .iloc[0]
        .trailing_turnover
        == 5
    )


def test_survivorship_impact_numeric():
    prices = pd.DataFrame(
        [
            {
                "trade_date": pd.Timestamp("2020-01-01"),
                "secid": "LIVE",
                "close": 10,
                "value": 10,
                "board": "TQBR",
            },
            {
                "trade_date": pd.Timestamp("2020-01-02"),
                "secid": "LIVE",
                "close": 12,
                "value": 10,
                "board": "TQBR",
            },
            {
                "trade_date": pd.Timestamp("2020-01-01"),
                "secid": "OLD",
                "close": 10,
                "value": 10,
                "board": "TQBR",
            },
            {
                "trade_date": pd.Timestamp("2020-01-02"),
                "secid": "OLD",
                "close": 8,
                "value": 10,
                "board": "TQBR",
            },
        ]
    )
    dynamic = pd.DataFrame(
        [{"trade_date": r.trade_date, "secid": r.secid, "eligible": True} for r in prices.itertuples()]
    )
    result = survivorship_comparison(prices, dynamic, ["LIVE"])
    assert len(result) == 1 and result.iloc[0].return_difference < 0 and result.iloc[0].difference < 0


def test_financial_membership_not_manual():
    assert coverage_suitability(0, 0, 0) == "insufficient_common_sample"


def test_multi_session_intraday():
    assert classify_multi_session("2024-01-01 09:00") == "morning"
    assert classify_multi_session("2024-01-01 10:05") == "opening_auction"
    assert classify_multi_session("2024-01-01 12:00") == "main"
    assert classify_multi_session("2024-01-01 18:50") == "closing_auction"
    assert classify_multi_session("2024-01-01 20:00") == "evening"


def test_ifrs_review_validation():
    record = {
        "document_id": "d",
        "metric": "profit",
        "source_page": "12",
        "source_table": "1",
        "source_line": "Net profit",
        "raw_text_fragment": "Net profit 100 RUB bn",
        "candidate_value": 100,
        "unit": "RUB bn",
        "confidence": 0.95,
    }
    assert validate_ifrs_review(record)["status"] == "validated"
    record["source_page"] = None
    assert validate_ifrs_review(record)["status"] == "requires_manual_review"


def test_option_history_vs_snapshot():
    assert distinguish_option_history(10, 0) == "snapshot_only"
    assert distinguish_option_history(10, 5) == "historical_pilot"
    assert distinguish_option_history(0, 0) == "unavailable"


def test_coverage_tiers_and_insufficient_sample():
    assert coverage_suitability(1200, 300, 6) == "ready_for_direction_model"
    assert coverage_suitability(300, 80, 3) == "ready_for_experimental_model"
    assert coverage_suitability(100, 80, 3) == "insufficient_common_sample"


def test_effective_sample_size():
    assert effective_sample_size([1, 2]) == 2
    assert 1 <= effective_sample_size([1, 0, 1, 0, 1, 0]) <= 6


def test_infer_expired_futures_date():
    assert infer_quarterly_expiration("SBRF-9.21") == date(2021, 9, 16)
    assert infer_quarterly_expiration("bad") is None
