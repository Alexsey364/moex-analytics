"""SBER fundamental dashboard pages, robust to empty and partial databases."""

import pandas as pd
import streamlit as st

from ..data_access import read_connection


def _read(query: str) -> pd.DataFrame:
    with read_connection() as con:
        try:
            return con.execute(query).df()
        except Exception:
            return pd.DataFrame()


def _empty(frame: pd.DataFrame) -> bool:
    if frame.empty:
        st.info("Недостаточно официальных point-in-time данных. Выполните контролируемый импорт отчёта.")
        return True
    return False


def render_fundamental():
    st.header("Фундаментал SBER")
    frame = _read(
        """SELECT metric_id,value,report_period_end,publication_date,age_days,source
        FROM fundamental_snapshots WHERE secid='SBER' AND trade_date=(
        SELECT max(trade_date) FROM fundamental_snapshots) ORDER BY metric_id"""
    )
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)


def render_multiples():
    st.header("Мультипликаторы SBER")
    frame = _read(
        """SELECT trade_date,metric_id,value,report_period_end,publication_date
        FROM fundamental_features WHERE secid='SBER' AND metric_id IN
        ('pe_ttm','pb','ptbv','dividend_yield','earnings_yield') ORDER BY trade_date"""
    )
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)


def render_scenarios():
    st.header("Сценарная оценка SBER")
    frame = _read(
        """SELECT as_of_date,scenario,method,fair_value,dividend,total_return,
        lower_price,upper_price FROM valuation_results WHERE secid='SBER'
        ORDER BY as_of_date DESC,scenario,method"""
    )
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)


def render_validation():
    st.header("Историческая проверка фундаментала")
    st.warning("Результаты описательные, пока число независимых отчётных релизов мало.")
    render_scenarios()


def render_sources():
    st.header("Источники и качество")
    releases = _read("SELECT * FROM fundamental_releases WHERE secid='SBER' ORDER BY publication_date DESC")
    issues = _read("SELECT * FROM fundamental_quality_issues WHERE secid='SBER' ORDER BY detected_at DESC")
    if not _empty(releases):
        st.dataframe(releases, use_container_width=True)
    if not issues.empty:
        st.dataframe(issues, use_container_width=True)
