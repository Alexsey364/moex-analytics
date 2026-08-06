"""SBER event and information intelligence dashboard pages."""

import pandas as pd
import streamlit as st

from ..data_access import read_connection


def _read(query):
    with read_connection() as con:
        try:
            return con.execute(query).df()
        except Exception:
            return pd.DataFrame()


def render_feed():
    st.header("Информационная лента SBER")
    frame = _read("""SELECT available_from,event_type,event_subtype,title,source_id,
    validation_status,relevance_to_sber,direction_hypothesis,source_url
    FROM sber_events ORDER BY available_from DESC""")
    if frame.empty:
        st.info("Validated события ещё не загружены.")
    else:
        st.dataframe(frame, width="stretch")


def render_calendar():
    st.header("Календарь SBER")
    frame = _read("""SELECT coalesce(scheduled_at,occurred_at) event_at,event_type,title,
    expected_status,severity,source_url FROM sber_events
    WHERE scheduled_at>=current_timestamp OR occurred_at>=current_timestamp
    ORDER BY event_at""")
    st.dataframe(frame, width="stretch") if not frame.empty else st.info("Нет подтверждённых будущих дат.")


def render_reactions():
    st.header("Реакция на события")
    st.warning("Совпадение события и движения цены не доказывает причинность.")
    st.dataframe(_read("SELECT * FROM sber_event_studies ORDER BY event_type,event_window"), width="stretch")


def render_expectations():
    st.header("Ожидания рынка")
    frame = _read("SELECT * FROM sber_expectations ORDER BY available_from DESC")
    if frame.empty:
        st.info("Исторический validated consensus не найден; coverage отсутствует.")
    else:
        st.dataframe(frame, width="stretch")


def render_changes():
    st.header("Что изменилось")
    frame = _read("SELECT * FROM sber_decision_change_log ORDER BY changed_at DESC")
    if frame.empty:
        st.info("Подтверждённые события не изменили текущее решение.")
    else:
        st.dataframe(frame, width="stretch")


def render_operational():
    st.header("Оперативная статистика SBER")
    st.dataframe(
        _read("""SELECT m.observation_date,m.metric_id,m.value,m.unit,m.source,
    m.available_from FROM sber_event_metrics m JOIN sber_events e USING(event_id)
    ORDER BY m.observation_date DESC"""),
        width="stretch",
    )


def render_quality():
    st.header("Информационное качество")
    state = _read("SELECT * FROM sber_live_information_state ORDER BY calculation_at DESC LIMIT 1")
    issues = _read("SELECT * FROM sber_event_quality_issues ORDER BY detected_at DESC")
    if not state.empty:
        st.dataframe(state, width="stretch")
    if not issues.empty:
        st.dataframe(issues, width="stretch")
