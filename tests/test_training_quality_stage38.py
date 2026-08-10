from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import requests

from moex_analytics.database import SCHEMA
from moex_analytics.macro.models import Observation
from moex_analytics.training_quality.context_expansion import (
    CONTEXT_EXPERIMENTS,
    FRED,
    _ablation,
    _context_frame,
    _coverage,
    _fred_download,
    _load_new_series,
    _series,
    _store_context,
    expand_predictive_context,
    predictive_context_status,
)
from moex_analytics.training_quality.schema import DDL


def test_context_experiments_cover_required_families():
    assert set(CONTEXT_EXPERIMENTS) == {
        "market_only", "market_sector", "market_fx", "market_rates",
        "market_commodity", "all_context",
    }
    assert FRED["fred_brent"][0] == "DCOILBRENTEU"


def test_fred_prices_are_conservatively_available_next_day():
    response = SimpleNamespace(
        content=b"observation_date,DCOILBRENTEU\n2020-01-02,65.5\n2020-01-03,.\n",
        raise_for_status=lambda: None,
    )
    rows, _ = _fred_download("fred_brent", SimpleNamespace(get=lambda *a, **k: response))
    assert len(rows) == 1
    assert rows[0].available_from.date() > rows[0].observation_date


def test_series_features_use_only_recorded_availability():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    con.execute("""INSERT INTO macro_observations VALUES
        ('x',DATE '2020-01-01',DATE '2020-01-01',TIMESTAMP '2020-01-02',100,
        'v',current_timestamp,'official'),
        ('x',DATE '2020-01-02',DATE '2020-01-02',TIMESTAMP '2020-01-03',110,
        'v',current_timestamp,'official')""")
    frame = _series(con, "x", "factor")
    assert frame.available_date.min().date().isoformat() == "2020-01-02"
    assert round(frame.factor_return_1.iloc[-1], 3) == 0.1


def test_context_materialization_exposures_ablation_and_orchestration(monkeypatch):
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    con.execute(DDL)
    con.execute("""CREATE TABLE moex_equity_eod(
        trade_date DATE,secid VARCHAR,close DOUBLE,value DOUBLE)""")
    con.execute("""CREATE TABLE zcyc_features(
        observation_date DATE,available_from TIMESTAMP,short_rate DOUBLE,long_rate DOUBLE,
        slope_10y_2y DOUBLE,parallel_shift DOUBLE)""")
    dates = pd.date_range("2018-01-01", periods=900, freq="D")
    asset = pd.DataFrame({"trade_date": dates.date, "secid": "SBERP",
                          "close": 100 + np.arange(900) * 0.03 + np.sin(np.arange(900) / 10),
                          "value": 1_000_000.0})
    con.register("asset_seed", asset)
    con.execute("INSERT INTO moex_equity_eod SELECT * FROM asset_seed")
    con.unregister("asset_seed")
    series_ids = ("moex_imoex", "moex_finance", "cbr_usd_rub", "cbr_key_rate",
                  "cbr_ruonia", "moex_rusfar", "fred_brent", "fred_henry_hub_gas",
                  "moex_rvi", "moex_rgbi")
    seed = pd.concat([
        pd.DataFrame({"series_id": sid, "observation_date": dates.date,
                      "release_date": dates.date,
                      "available_from": dates + pd.Timedelta(days=1),
                      "value": 100 + np.arange(900) * 0.01 + offset,
                      "vintage": "v", "loaded_at": dates, "source": "official"})
        for offset, sid in enumerate(series_ids)
    ])
    con.register("macro_seed", seed)
    con.execute("INSERT INTO macro_observations SELECT * FROM macro_seed")
    con.unregister("macro_seed")
    zcyc = pd.DataFrame({"observation_date": dates.date,
                         "available_from": dates + pd.Timedelta(days=1),
                         "short_rate": 10.0, "long_rate": 12.0,
                         "slope_10y_2y": 2.0, "parallel_shift": 0.0})
    con.register("zcyc_seed", zcyc)
    con.execute("INSERT INTO zcyc_features SELECT * FROM zcyc_seed")
    con.unregister("zcyc_seed")
    frame = _context_frame(con, "SBERP")
    assert frame.market.notna().sum() > 800
    monkeypatch.setattr("moex_analytics.training_quality.context_expansion.SECIDS", ("SBERP",))
    monkeypatch.setattr("moex_analytics.training_quality.context_expansion.HORIZONS", (20,))
    synchronized, exposures = _store_context(con, "run")
    assert synchronized == exposures == 900
    assert _ablation(con, "run") == 6
    _coverage(con, "run")
    assert con.execute("SELECT count(*) FROM predictive_context_coverage").fetchone()[0] == 12
    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion._load_new_series",
        lambda *args: (0, 0, {}),
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion._store_context",
        lambda *args: (10, 10),
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion._ablation", lambda *args: 6
    )
    result = expand_predictive_context(con)
    assert result["production_changes"] == 0
    assert result["new_model_families"] == 0
    assert predictive_context_status(con)["latest"][0] == result["run_id"]


def test_new_series_loader_is_idempotent_and_records_source_errors(monkeypatch, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    observed = pd.Timestamp("2020-01-01").date()
    available = pd.Timestamp("2020-01-02", tz="UTC").to_pydatetime()

    def observation(series_id):
        return Observation(series_id, observed, observed, available, 100.0, "v", "official")

    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion.moex.download",
        lambda series_id, *args: [observation(series_id)],
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion._fred_download",
        lambda series_id, *args: ([observation(series_id)], b"date,value\n2020-01-01,100"),
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion.PROJECT_ROOT", tmp_path
    )
    requests_count, rows, errors = _load_new_series(con)
    assert (requests_count, rows, errors) == (4, 4, {})
    requests_count, rows, errors = _load_new_series(con)
    assert (requests_count, rows, errors) == (2, 2, {})

    con.execute("DELETE FROM macro_observations")

    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion.moex.download", fail
    )
    monkeypatch.setattr(
        "moex_analytics.training_quality.context_expansion._fred_download", fail
    )
    requests_count, rows, errors = _load_new_series(con)
    assert requests_count == 4 and rows == 0
    assert set(errors) == {"moex_telecom", "moex_chemicals", *FRED}
