from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

from moex_analytics.database import SCHEMA
from moex_analytics.macro.calendar_alignment import align_point_in_time, external_available_from
from moex_analytics.macro.models import Observation
from moex_analytics.macro.quality import inspect_observations
from moex_analytics.macro.repository import upsert_observations
from moex_analytics.macro.sources.cbr import (
    key_rate_decision,
    parse_currency_xml,
    parse_key_rate_html,
    parse_ruonia_html,
)
from moex_analytics.macro.sources.moex import normalize_history
from moex_analytics.macro.transformations import market_transform, rate_transform, relative_features
from moex_analytics.macro.validation import (
    LogisticModel,
    RidgeModel,
    classification_metrics,
    empirical_intervals,
    price_interval,
    regression_metrics,
    walk_forward_splits,
)

MOSCOW = ZoneInfo("Europe/Moscow")


def test_cbr_currency_normalizes_nominal_and_effective_date():
    xml = '<ValCurs><Record Date="02.01.2024"><Nominal>10</Nominal><Value>125,00</Value></Record></ValCurs>'
    row = parse_currency_xml(xml, "R01375")[0]
    assert row.value == 12.5
    assert row.available_from.date() == row.observation_date


def test_cbr_key_rate_keeps_only_decisions_at_publication_time():
    html = (
        '<table class="data"><tr><td>03.01.2024</td><td>16,00</td></tr>'
        "<tr><td>02.01.2024</td><td>15,00</td></tr></table>"
    )
    rows = parse_key_rate_html(html)
    assert [row.value for row in rows] == [15.0, 16.0]
    assert rows[-1].available_from.hour == 13


def test_ruonia_uses_explicit_release_and_conservative_next_day():
    cells = "".join(
        f"<td>{value}</td>"
        for value in ["02.01.2024", "15,1", "1", "1", "1", "1", "1", "1", "1", "ok", "03.01.2024"]
    )
    row = parse_ruonia_html(f'<table class="data"><tr>{cells}</tr></table>')[0]
    assert row.release_date == date(2024, 1, 3)
    assert row.available_from.date() == date(2024, 1, 4)


def test_moex_close_is_available_only_after_session():
    payload = {"history": {"columns": ["TRADEDATE", "CLOSE"], "data": [["2024-01-02", 100.0]]}}
    row = normalize_history("moex_finance", payload)[0]
    assert row.available_from.hour == 18 and row.available_from.minute == 50


def test_point_in_time_alignment_never_uses_future_cpi_or_rate():
    sessions = pd.DataFrame({"cutoff": pd.to_datetime(["2024-01-09T15:00Z", "2024-01-10T15:00Z"])})
    observations = pd.DataFrame(
        {
            "available_from": pd.to_datetime(["2024-01-10T13:30Z"]),
            "value": [7.5],
            "observation_date": [date(2023, 12, 31)],
        }
    )
    result = align_point_in_time(sessions, observations)
    assert pd.isna(result.iloc[0]["value"])
    assert result.iloc[1]["value"] == 7.5


def test_external_close_after_moex_is_not_moved_back():
    foreign = datetime(2024, 1, 2, 23, tzinfo=MOSCOW)
    moex = datetime(2024, 1, 2, 18, 50, tzinfo=MOSCOW)
    assert external_available_from(foreign, moex) == foreign


def test_repository_is_idempotent():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    row = key_rate_decision(date(2024, 1, 1), 16.0)
    assert upsert_observations(con, [row]) == 1
    assert upsert_observations(con, [row]) == 0


def test_quality_flags_invalid_dates_and_duplicates():
    frame = pd.DataFrame(
        [
            {
                "series_id": "x",
                "observation_date": date(2024, 2, 1),
                "release_date": date(2024, 1, 1),
                "available_from": "2023-12-31T00:00Z",
                "value": 1.0,
                "vintage": "v",
            },
            {
                "series_id": "x",
                "observation_date": date(2024, 2, 1),
                "release_date": date(2024, 1, 1),
                "available_from": "2023-12-31T00:00Z",
                "value": 1.0,
                "vintage": "v",
            },
        ]
    )
    kinds = {issue["issue_type"] for issue in inspect_observations(frame)}
    assert {"duplicate", "observation_after_release", "available_before_release"} <= kinds


def test_macro_transformations_use_trailing_windows():
    values = pd.Series(np.arange(1.0, 301.0))
    result = market_transform(values, "fx")
    assert result.loc[20, "fx_return_20"] == 20.0
    rates = rate_transform(values, values / 2, values - 1)
    assert rates.loc[252, "key_rate_change_12m"] == 252
    relative = relative_features(values, values * 2, values * 3)
    assert "asset_sector_beta_60" in relative


def test_walk_forward_is_ordered_and_has_no_shuffle():
    splits = list(walk_forward_splits(12, 6, 3, 3))
    assert splits[0][0].tolist() == list(range(6))
    assert splits[0][1].tolist() == [6, 7, 8]
    assert max(splits[0][0]) < min(splits[0][1])


def test_linear_logistic_baselines_metrics_and_intervals():
    x = np.arange(20.0).reshape(-1, 1)
    y = x[:, 0] * 0.1
    predicted = RidgeModel(alpha=0.01).fit(x, y).predict(x)
    assert regression_metrics(y, predicted)["rmse"] < 0.01
    probability = LogisticModel().fit(x, y > np.median(y)).predict_proba(x)[:, 1]
    classification = classification_metrics(y > np.median(y), probability)
    assert classification["brier"] < 0.25
    assert classification["roc_auc"] > 0.5
    assert classification["calibration"]
    intervals = empirical_intervals(0.1, [-0.2, 0, 0.2])
    assert intervals["lower_90"] < intervals["upper_90"]
    assert price_interval(100, -0.1, 0.2) == (90, 120)


def test_observation_model_separates_observation_release_and_availability():
    row = Observation(
        "cpi",
        date(2024, 1, 31),
        date(2024, 2, 9),
        datetime(2024, 2, 9, 19, tzinfo=MOSCOW),
        7.4,
        "v1",
        "Rosstat",
    )
    assert row.observation_date < row.release_date
    assert row.available_from.date() == row.release_date
