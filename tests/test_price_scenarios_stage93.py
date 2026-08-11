import duckdb

from moex_analytics.analog_projection.schema import DDL as PROJECTION_DDL
from moex_analytics.price_scenarios import build_price_scenarios


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(PROJECTION_DDL)
    con.execute(
        "INSERT INTO analog_projection_runs VALUES "
        "('p',now(),'2026-01-30','s','t',9,1,'v','completed',true,true,true,'{}')"
    )
    for horizon in (1, 5, 20, 40, 60, 80, 100, 120, 250):
        con.execute(
            """INSERT INTO analog_projection_horizons VALUES
            ('p','SBERP',?,'ready',100,105,.05,90,95,110,115,6,-.08,-.2,.3,4,'2012-01-01',true)""",
            [horizon],
        )
    returns = (-0.20, -0.05, 0.02, 0.08, 0.15, 0.30)
    for index, terminal in enumerate(returns):
        analog = f"20{10 + index}-01-10"
        for session in range(1, 251):
            value = terminal * session / 250
            if index == 3 and session < 100:
                value -= 0.08
            con.execute(
                "INSERT INTO analog_projected_paths VALUES ('p','SBERP',?,?,?, ?,100,?,.9,false,true)",
                [analog, session, analog, value, 100 * (1 + value)],
            )
    return con


def test_layers_never_blend_without_frozen_oos_proof() -> None:
    con = _con()
    result = build_price_scenarios(con)
    assert result["consensus_usable"] == 0
    assert result["production_changes"] == 0 and not result["probability_gate_changed"]
    assert con.execute(
        "SELECT bool_and(consensus_status='not_validated') FROM price_scenario_layers"
    ).fetchone()[0]
    assert build_price_scenarios(con)["idempotent"] is True


def test_branches_are_real_medoid_episodes_and_touch_counts_not_probabilities() -> None:
    con = _con()
    build_price_scenarios(con)
    observed = con.execute(
        """SELECT branch,episodes,medoid_analog_date,status FROM price_scenario_branches
        WHERE secid='SBERP' AND episodes>0"""
    ).fetchall()
    assert observed and all(row[2] is not None and row[3] == "historical_cluster" for row in observed)
    touch = con.execute(
        "SELECT analog_count,touch_down_5,touch_up_5 FROM price_scenario_touch_memory "
        "WHERE secid='SBERP' AND horizon=250"
    ).fetchone()
    assert touch[0] == 6 and 0 <= touch[1] <= 6 and 0 <= touch[2] <= 6


def test_x5_remains_explicitly_insufficient() -> None:
    con = _con()
    build_price_scenarios(con)
    assert con.execute(
        "SELECT count(*) FROM price_scenario_branches WHERE secid='X5' AND status='insufficient_history'"
    ).fetchone()[0] == 4
