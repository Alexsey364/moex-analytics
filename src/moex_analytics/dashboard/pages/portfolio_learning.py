"""Portfolio-aware learning research dashboard."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.portfolio_learning import portfolio_learning_status


def render() -> None:
    st.header("Portfolio-aware predictive allocation")
    with read_connection() as con:
        status = portfolio_learning_status(con, ensure=False)
        if not status["latest"]:
            st.info("Portfolio-aware research ещё не выполнялся.")
            return
        run_id = status["latest"][0]
        candidates = con.execute(
            """SELECT secid,tranche,lots,invested,delta_weight,delta_volatility,
            delta_concentration,predictive_attractiveness,fundamental_attractiveness,
            valuation,dividend,standalone_risk,diversification_benefit,
            concentration_cost,portfolio_rank,eligible,status,reason
            FROM portfolio_marginal_candidates WHERE run_id=?
            ORDER BY eligible DESC,portfolio_rank DESC""",
            [run_id],
        ).df()
        backtest = con.execute(
            "SELECT * FROM portfolio_learning_backtests WHERE run_id=? ORDER BY method", [run_id]
        ).df()
    st.dataframe(candidates, use_container_width=True, hide_index=True)
    st.info("CASH — полноценный вариант; система не обязана распределять весь транш.")
    with st.expander("Историческая research-only симуляция"):
        st.dataframe(backtest, use_container_width=True, hide_index=True)
