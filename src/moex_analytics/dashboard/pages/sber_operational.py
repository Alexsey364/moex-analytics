"""Operational SBER dashboard pages."""

import streamlit as st

from moex_analytics.database import connection


def _table(title, query):
    st.header(title)
    with connection(read_only=True) as con:
        st.dataframe(con.execute(query).df(), use_container_width=True)


def render_business():
    _table(
        "Оперативный бизнес SBER",
        "select * from sber_daily_operating_state order by trade_date desc limit 100",
    )


def render_nowcast():
    _table("Nowcast SBER", "select * from sber_nowcasts order by as_of_date desc,method,metric_id")


def render_warnings():
    _table(
        "Ранние предупреждения", "select * from sber_operating_indicators order by as_of_date desc,direction"
    )


def render_zone_audit():
    _table("Аудит зон покупки", "select * from sber_price_zone_audit order by as_of_date desc,lower_bound")


def render_size():
    _table("Почему такая доля", "select * from sber_position_size_explanation order by as_of_date desc")


def render_journal():
    _table("Журнал решений", "select * from sber_live_decisions order by created_at desc")


def render_outcomes():
    _table("Реальные результаты", "select * from sber_live_outcomes order by matured_at desc,horizon")


def render_versions():
    _table("Версии модели", "select * from sber_frozen_rules order by activated_at desc")
