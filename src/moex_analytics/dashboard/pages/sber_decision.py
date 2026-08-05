"""Dashboard pages for the explainable SBER decision."""

import pandas as pd
import streamlit as st

from ..data_access import read_connection


def _read(query):
    with read_connection() as con:
        try:
            return con.execute(query).df()
        except Exception:
            return pd.DataFrame()


def render_decision():
    st.header("Решение по SBER")
    result = _read("SELECT * FROM sber_decision_results ORDER BY as_of_date DESC LIMIT 1")
    if result.empty:
        st.info("Решение ещё не рассчитано или данных недостаточно.")
        return
    r = result.iloc[0]
    cols = st.columns(5)
    cols[0].metric("Цена", f"{r.current_price:.1f} ₽")
    cols[1].metric("Статус", r.decision_status)
    cols[2].metric("Горизонт", f"{r.horizon} сессий")
    cols[3].metric("Confidence", f"{r.decision_confidence:.1f}/100")
    cols[4].metric("Первая доля", f"{r.first_position_fraction:.0%}")
    st.subheader("Почему")
    st.write(r.explanation)
    st.subheader("Что делать сейчас")
    st.dataframe(
        _read(
            f"SELECT zone_name,lower_bound,upper_bound,action,max_position_fraction FROM sber_price_zones WHERE run_id='{r.run_id}' ORDER BY lower_bound"
        ),
        width="stretch",
    )
    st.subheader("Что должно измениться")
    st.dataframe(
        _read(
            f"SELECT category,condition_text,decision_change FROM sber_decision_triggers WHERE run_id='{r.run_id}'"
        ),
        width="stretch",
    )
    st.subheader("Независимые блоки")
    st.dataframe(
        _read(
            f"SELECT block_id,score,confidence,status,data_date FROM sber_decision_evidence WHERE run_id='{r.run_id}'"
        ),
        width="stretch",
    )


def render_reporting():
    st.header("МСФО и РСБУ SBER")
    st.dataframe(
        _read(
            "SELECT accounting_standard,period_end,metric_id,normalized_value,quality_status,source_page,source_table FROM fundamental_metric_values ORDER BY period_end DESC"
        ),
        width="stretch",
    )


def render_dividend():
    st.header("Дивиденд SBER")
    st.warning("Сценарный DPS не является объявленным дивидендом.")
    st.dataframe(
        _read("SELECT * FROM sber_dividend_outlook ORDER BY as_of_date DESC,scenario"),
        width="stretch",
    )


def render_valuation():
    st.header("Оценка SBER")
    st.dataframe(
        _read("SELECT * FROM sber_valuation_ensemble ORDER BY as_of_date DESC,scenario,method"),
        width="stretch",
    )


def render_zones():
    render_decision()


def render_triggers():
    render_decision()


def render_backtest():
    st.header("Историческая проверка решения")
    st.dataframe(
        _read(
            "SELECT strategy,horizon,count(*) observations,avg(total_return) avg_return,min(max_drawdown) worst_drawdown FROM sber_decision_backtest GROUP BY strategy,horizon ORDER BY horizon,strategy"
        ),
        width="stretch",
    )
