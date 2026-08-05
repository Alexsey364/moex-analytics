from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest
import yaml

from moex_analytics.database import SCHEMA
from moex_analytics.fundamentals.features import annualize, bvps, eps, payout, roe, ttm
from moex_analytics.fundamentals.models import FundamentalObservation
from moex_analytics.fundamentals.parser import parse_file
from moex_analytics.fundamentals.point_in_time import build_snapshots
from moex_analytics.fundamentals.quality import inspect
from moex_analytics.fundamentals.repository import available_as_of, upsert_observations
from moex_analytics.fundamentals.scenarios import (
    calculate_all,
    calculate_scenario,
    load_scenarios,
    sensitivity,
)
from moex_analytics.fundamentals.sources.cbr import discover as discover_cbr
from moex_analytics.fundamentals.sources.sber import discover as discover_sber
from moex_analytics.fundamentals.validation import metrics, price_after_sessions
from moex_analytics.fundamentals.valuation import dividend_discount, justified_pb, pb, pe, pe_scenario


def obs(publication="2024-02-29", revision="original", value=100.0, standard="IFRS"):
    d = date.fromisoformat(publication)
    return FundamentalObservation(
        "net_profit",
        date(2023, 1, 1),
        date(2023, 12, 31),
        "annual",
        standard,
        d,
        datetime(d.year, d.month, d.day, 19, tzinfo=ZoneInfo("Europe/Moscow")),
        value,
        "RUB",
        "SBER",
        "official",
        revision,
    )


def test_point_in_time_and_revision():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    upsert_observations(con, [obs(), obs("2024-04-01", "revision-1", 110)])
    assert available_as_of(con, date(2024, 2, 28)).empty
    assert available_as_of(con, date(2024, 3, 1)).value.tolist() == [100]
    assert available_as_of(con, date(2024, 4, 2)).value.tolist() == [110]


def test_standards_are_separate():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    upsert_observations(con, [obs(), obs(standard="RAS")])
    assert len(available_as_of(con, date(2024, 3, 1))) == 2


def test_features():
    assert ttm([1, 2, 3, 4]) == 10 and ttm([1, 2, None, 4]) is None
    assert annualize(30, 3) == 120 and eps(100, 20) == 5 and bvps(200, 20) == 10
    assert roe(20, 90, 110) == 0.2 and payout(2.5, 5) == 0.5


def test_valuation_formulas():
    assert pe(100, 20) == 5 and pb(100, 50) == 2 and pe_scenario(100, 20, 5) == 25
    assert justified_pb(0.2, 0.05, 0.15) == pytest.approx(1.5)
    assert justified_pb(0.1, 0.1, 0.1) is None and dividend_discount(10, 0.2, 0.05) == pytest.approx(70)


def test_scenarios_and_sensitivity():
    a = {
        "profit": 100,
        "shares": 10,
        "bvps": 20,
        "roe": 0.2,
        "growth": 0.02,
        "cost_of_equity": 0.15,
        "payout_ratio": 0.5,
        "target_pe": 5,
        "target_pb": 1.2,
    }
    rows = calculate_scenario("base", a, 40)
    assert len(rows) == 3 and rows[0]["dividend"] == 5
    assert len(sensitivity([100, 120], [4, 5], 10)) == 4


def test_validation_insufficient_and_metrics():
    assert metrics(pd.DataFrame())["status"] == "insufficient_data"
    frame = pd.DataFrame(
        {
            "current_price": [100],
            "actual_price": [120],
            "median_price": [115],
            "lower_price": [90],
            "upper_price": [130],
        }
    )
    assert metrics(frame)["interval_coverage"] == 1


def test_controlled_csv_parser_and_quality(tmp_path):
    path = tmp_path / "report.csv"
    path.write_text(
        "metric_id,period_start,period_end,report_type,accounting_standard,publication_date,"
        "value,unit,source,source_document,revision_id\n"
        "net_profit,2023-01-01,2023-12-31,annual,IFRS,2024-02-29,100,RUB,SBER,doc,original\n",
        encoding="utf-8",
    )
    rows = parse_file(path)
    assert len(rows) == 1 and inspect(rows) == []
    assert inspect([rows[0], rows[0]])[0]["issue_type"] == "duplicate"
    with pytest.raises(ValueError):
        parse_file(tmp_path / "report.pdf")


def test_snapshot_and_scenario_config(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    con.execute(
        """INSERT INTO canonical_daily_prices(trade_date,canonical_secid,close)
        VALUES ('2024-03-01','SBER',300)"""
    )
    upsert_observations(con, [obs()])
    assert build_snapshots(con) == 1
    config = {
        "sber_valuation": {
            "version": "test",
            "scenarios": {
                "base": {
                    "profit": "100",
                    "shares": "10",
                    "bvps": 20,
                    "roe": 0.2,
                    "growth": 0.02,
                    "cost_of_risk": 0.01,
                    "cost_of_equity": 0.15,
                    "payout_ratio": 0.5,
                    "target_pe": 5,
                    "target_pb": 1.2,
                }
            },
        }
    }
    path = tmp_path / "scenarios.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert load_scenarios(path)["version"] == "test"
    assert calculate_all(con, path)["rows"] == 3


def test_source_catalogues_and_future_price():
    assert discover_sber() and discover_cbr()
    prices = pd.DataFrame({"trade_date": pd.date_range("2024-01-01", periods=251), "close": range(251)})
    assert price_after_sessions(prices, pd.Timestamp("2024-01-01")) == 250
