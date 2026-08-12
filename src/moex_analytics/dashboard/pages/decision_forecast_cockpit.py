"""Stage 108 research forecast cockpit, separate from production decisions."""

from __future__ import annotations

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection


def load_return_forecast(secid: str) -> dict:
    with read_connection() as con:
        run = con.execute("select run_id,cutoff from dynamic_ensemble_runs where status='completed' "
                          "order by finished_at desc limit 1").fetchone()
        if not run:
            return {"available": False}
        forecasts = con.execute("select horizon,expected_return,expected_drawdown,probability_up,"
            "probability_allowed,disagreement,confidence,status,best_model from "
            "dynamic_ensemble_forecasts where run_id=? and secid=? order by horizon",
            [run[0], secid]).df()
        components = con.execute("select horizon,component,prediction,reliability,weight,included,reason "
            "from dynamic_ensemble_components where run_id=? and secid=? order by horizon,component",
            [run[0], secid]).df()
        price = con.execute("select arg_max(total_return_index,trade_date) from daily_returns "
                            "where canonical_secid=?", [secid]).fetchone()[0]
        ranking = con.execute("select horizon,relative_rank,rank_low,rank_high,status from "
            "current_portfolio_ranking where secid=? qualify row_number() over(partition by horizon "
            "order by cutoff desc)=1 order by horizon", [secid]).df()
        fundamental = con.execute("select horizon,dividend_component,earnings_component,"
            "rerating_component,expected_total_return,fair_value_low,fair_value_high,status "
            "from fundamental_return_estimates where secid=? qualify row_number() over "
            "(partition by horizon order by as_of_date desc)=1 order by horizon", [secid]).df()
    return {"available": True, "run_id": run[0], "cutoff": run[1], "price": price,
            "forecasts": forecasts, "components": components, "ranking": ranking,
            "fundamental": fundamental}


def render(secid: str = "SBERP") -> None:
    payload = load_return_forecast(secid)
    st.subheader("Прогноз доходности — исследовательский cockpit")
    if not payload["available"]:
        st.info("Недостаточно данных: Stage 107 ensemble ещё не рассчитан.")
        return
    st.caption(f"Cutoff {payload['cutoff']} · production-модель не изменена")
    table = payload["forecasts"].copy()
    table["Эквивалент цены"] = payload["price"] * (1 + table.expected_return)
    table["Ожидаемая доходность"] = table.expected_return.map(lambda x: f"{x:.1%}")
    table["Надёжность"] = table.status
    table["P(up)"] = table.apply(
        lambda row: f"{row.probability_up:.1%}" if row.probability_allowed else "не публикуется",
        axis=1,
    )
    st.dataframe(table[["horizon", "Ожидаемая доходность", "Эквивалент цены", "P(up)",
                        "Надёжность", "best_model"]], hide_index=True, use_container_width=True)
    if (table.status == "NO_PROVEN_FORECAST_EDGE").any():
        st.warning("NO PROVEN FORECAST EDGE: сложные модели не победили сильный baseline.")
    st.markdown("**Cross-sectional position**")
    st.dataframe(payload["ranking"], hide_index=True, use_container_width=True)
    st.markdown("**Fundamental valuation range (не predictive interval)**")
    st.dataframe(payload["fundamental"], hide_index=True, use_container_width=True)
    with st.expander("Веса и причины включения моделей"):
        st.dataframe(payload["components"], hide_index=True, use_container_width=True)
