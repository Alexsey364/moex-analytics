"""Dashboard for critical predictive-data completion."""

import streamlit as st

from moex_analytics.database import connection


def _show(title, query):
    st.header(title)
    with connection() as con:
        try:
            st.dataframe(con.execute(query).df(), use_container_width=True)
        except Exception as exc:
            st.info(f"Данные ещё не построены: {exc}")


def render_universe():
    _show("Историческая вселенная", "select * from historical_equity_universe order by is_traded,secid")


def render_survivorship():
    _show("Survivorship bias", "select * from critical_breadth order by trade_date desc,universe_kind")


def render_finance():
    _show(
        "Финансовый сектор",
        "select * from critical_breadth where universe_kind like 'financial%' order by trade_date desc",
    )


def render_zcyc():
    _show("Кривая ZCYC", "select * from zcyc_features order by observation_date desc")


def render_futures():
    _show("Фьючерсы SBER", "select * from sber_continuous_futures order by trade_date desc")


def render_rolls():
    _show("Roll history", "select * from sber_futures_rolls order by roll_date desc")


def render_ifrs():
    _show("МСФО SBER", "select * from sber_ifrs_discovery order by publication_date desc nulls last")


def render_options():
    _show("Опционы: доступность и качество", "select * from moex_options_audit order by expiration desc")


def render_intraday():
    _show("Внутридневные сессии", "select * from intraday_features order by trade_date desc")


def render_readiness():
    _show("Готовность данных к прогнозу", "select * from critical_source_catalog order by dataset_id")


def render_ablation():
    _show("Повторный ablation", "select * from critical_ablation_results order by horizon,block_name")
