from datetime import date

import duckdb
import numpy as np
import pandas as pd

import moex_analytics.portfolio_scenarios.core as scenarios
from moex_analytics.state_similarity.schema import DDL as STATE_DDL


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(STATE_DDL)
    dates = pd.bdate_range(date(2018, 1, 1), periods=700)
    con.execute(
        "INSERT INTO state_similarity_runs VALUES "
        "('state',now(),?,1,30,15,'v',true,true,true,'completed','{}')",
        [dates[-1].date()],
    )
    analog_dates = dates[100:500:50]
    for index, analog_date in enumerate(analog_dates):
        con.execute(
            "INSERT INTO state_similarity_matches VALUES ('state','AAA','state',?,?,?,.8,'{}',?,true,true)",
            [analog_date.date(), index + 1, index / 10, analog_date.date()],
        )
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)")
    rng = np.random.default_rng(89)
    market = 1000 * np.cumprod(1 + rng.normal(0.0002, 0.012, len(dates)))
    stock = 100 * np.cumprod(1 + rng.normal(0.0003, 0.015, len(dates)))
    for secid, values in (("IMOEX", market), ("AAA", stock)):
        con.executemany(
            "INSERT INTO canonical_daily_prices VALUES (?,?,?)",
            [(secid, day.date(), float(value)) for day, value in zip(dates, values, strict=True)],
        )
    con.execute(
        "CREATE TABLE whole_market_state_daily(trade_date DATE,available_from TIMESTAMP,"
        "market_state_label VARCHAR,breadth_json JSON,rates_json JSON,fx_json JSON,"
        "commodities_json JSON,volatility_json JSON)"
    )
    con.execute(
        "INSERT INTO whole_market_state_daily VALUES (?,now(),'stress','{}','{}','{}','{}','{}')",
        [dates[-1].date()],
    )
    con.execute(
        "CREATE TABLE news_stories(headline VARCHAR,event_type VARCHAR,reliability VARCHAR,"
        "last_update_at TIMESTAMP,status VARCHAR,first_report_at TIMESTAMP)"
    )
    con.execute("INSERT INTO news_stories VALUES ('story','oil','official',now(),'active','2020-01-01')")
    return con


def test_scenario_tree_uses_real_independent_paths_and_historical_counts(monkeypatch) -> None:
    con = _con()
    monkeypatch.setattr(scenarios, "SECIDS", ("AAA",))
    first = scenarios.build_portfolio_scenario_tree(con)
    second = scenarios.build_portfolio_scenario_tree(con)
    assert first["episodes"] > 0 and first["branches"] > 0
    assert not first["idempotent"] and second["idempotent"]
    assert con.execute("SELECT bool_and(observed AND immutable) FROM portfolio_scenario_paths").fetchone()[0]
    assert con.execute(
        "SELECT bool_and(news_weights_changed=false) FROM portfolio_scenario_roots"
    ).fetchone()[0]
    texts = [
        row[0]
        for row in con.execute(
            "SELECT historical_frequency_text FROM portfolio_scenario_branches"
        ).fetchall()
    ]
    assert all("исторических" in text and " из " in text and "%" not in text for text in texts)
    assert con.execute("SELECT count(*) FROM portfolio_scenario_sensitivities").fetchone()[0] > 0


def test_fixed_scenario_labels_are_descriptive_not_probabilities() -> None:
    assert scenarios._scenario(np.array([-0.12, -0.04, 0.02]))[0] == "stress_recovery"
    assert scenarios._scenario(np.array([0.01, 0.08, 0.15]))[0] == "strong_rebound"
    assert scenarios._scenario(np.array([-0.02, -0.06, -0.1]))[0] == "renewed_decline"
    assert scenarios._scenario(np.array([-0.01, 0.01, 0.02]))[0] == "sideways_stabilization"
