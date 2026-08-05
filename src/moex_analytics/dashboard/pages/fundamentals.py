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


def render_reporting_history():
    st.header("История отчётности SBER")
    frame = _read("""SELECT period_end,accounting_standard,publication_date,title,
      processing_status,validation_status,source_url FROM fundamental_documents ORDER BY period_end DESC""")
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)


def render_fundamental_history():
    st.header("История фундаментала SBER")
    frame = _read("""SELECT trade_date,metric_id,value,report_period_end,publication_date
      FROM fundamental_features WHERE secid='SBER' ORDER BY trade_date,metric_id""")
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)
        chart = frame[frame.metric_id.isin(["eps", "bvps", "pe", "pb"])].pivot(
            index="trade_date", columns="metric_id", values="value"
        )
        st.line_chart(chart)


def render_current_valuation():
    st.header("Текущая оценка SBER")
    confidence = _read("SELECT * FROM fundamental_confidence ORDER BY as_of_date DESC LIMIT 1")
    values = _read("""SELECT scenario,method,fair_value,dividend,total_return,details_json
      FROM valuation_results WHERE scenario_version='sber-fact-valuation-v1'
      AND as_of_date=(SELECT max(as_of_date) FROM valuation_results) ORDER BY scenario,method""")
    if not confidence.empty:
        st.dataframe(confidence, use_container_width=True)
    if not _empty(values):
        st.dataframe(values, use_container_width=True)


def render_valuation_history():
    st.header("История оценок")
    frame = _read("SELECT * FROM fundamental_backtest_results ORDER BY valuation_date DESC,method,horizon")
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)


def render_error_history():
    st.header("История ошибок фундаментала")
    frame = _read("SELECT * FROM fundamental_model_comparison ORDER BY period,model,horizon")
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)


def render_fundamental_quality():
    st.header("Качество фундаментальных данных")
    frame = _read("""SELECT period_end,title,processing_status,validation_status,notes
      FROM fundamental_documents WHERE validation_status<>'validated' ORDER BY period_end DESC""")
    if not _empty(frame):
        st.dataframe(frame, use_container_width=True)
