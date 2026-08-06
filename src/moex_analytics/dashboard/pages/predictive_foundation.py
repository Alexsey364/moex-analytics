"""Predictive-foundation dashboard pages."""

import streamlit as st

from moex_analytics.database import connection


def _show(title, query):
    st.header(title)
    with connection(read_only=True) as con:
        st.dataframe(con.execute(query).df(), use_container_width=True)


def render_catalog():
    _show("Карта данных прогноза", "select * from predictive_data_catalog order by category,dataset_id")


def render_market():
    _show(
        "Состояние всего рынка", "select * from sber_relative_market_state order by trade_date desc limit 500"
    )


def render_breadth():
    _show("Ширина рынка", "select * from predictive_market_breadth order by trade_date desc limit 500")


def render_finance():
    _show("Финансовый сектор", "select * from sber_relative_market_state order by trade_date desc limit 500")


def render_futures():
    _show("Фьючерсы SBER", "select * from predictive_derivative_instruments order by secid")


def render_options():
    _show(
        "Опционы и ожидаемая волатильность",
        "select * from predictive_data_catalog where category='derivatives'",
    )


def render_rates():
    _show(
        "Ставки и кривая ОФЗ",
        "select * from predictive_yield_curve order by observation_date desc,tenor_years",
    )


def render_liquidity():
    _show("Ликвидность и перетоки", "select * from predictive_data_catalog where category='liquidity'")


def render_cross_market():
    _show("Межрыночные связи", "select * from predictive_lead_lag order by pair_id,lag")


def render_regimes():
    _show("Структурные режимы", "select * from structural_regimes order by effective_from desc")


def render_coverage():
    _show("Полнота данных", "select * from predictive_coverage_audit order by dataset_id,series_id")


def render_ablation():
    _show(
        "Добавочная ценность блоков", "select * from predictive_ablation_results order by horizon,block_name"
    )
