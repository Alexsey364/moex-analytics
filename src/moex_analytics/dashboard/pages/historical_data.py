"""Historical coverage pages for basic and advanced dashboard modes."""

import pandas as pd
import streamlit as st

from moex_analytics.database import connection
from moex_analytics.historical_data.core import ensure_schema


def _coverage() -> pd.DataFrame:
    with connection(read_only=False) as con:
        ensure_schema(con)
        return con.execute("SELECT * FROM historical_data_coverage ORDER BY instrument,dataset_family").df()


def render_advanced() -> None:
    st.header("Покрытие исторических данных")
    frame = _coverage()
    if frame.empty:
        st.info("Аудит ещё не выполнен. Запустите complete-historical-data-audit.")
        return
    colors = {"complete": "🟢", "partial": "🟡", "missing": "🔴"}
    display_status = frame.current_status.map(colors).fillna("🔴")
    display_status = display_status.mask(frame.access_class == "paid/restricted", "🔵")
    frame.insert(0, "coverage", display_status)
    selected = st.selectbox("Инструмент", ["Все", *sorted(frame.instrument.unique())])
    if selected != "Все":
        frame = frame[frame.instrument == selected]
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption("Синий статус означает платный или ограниченный источник; красный — данных нет.")


def render_basic() -> None:
    st.header("Качество данных")
    frame = _coverage()
    if frame.empty:
        st.warning("Покрытие ещё не рассчитано. Решения используют только ранее проверенные данные.")
        return
    good = frame[frame.current_status == "complete"]
    partial = frame[frame.current_status == "partial"]
    missing = frame[frame.current_status == "missing"]
    st.success(f"Что собрано хорошо: {len(good)} наборов")
    st.warning(f"Что собрано частично: {len(partial)} наборов")
    st.error(f"Чего не хватает: {len(missing)} наборов")
    critical = missing[missing.analytical_priority.isin(["critical", "high"])]
    if critical.empty:
        st.info("Критических незакрытых пробелов, влияющих на текущее решение, не выявлено.")
    else:
        st.info(f"На надёжность решения сейчас могут влиять {len(critical)} приоритетных пробелов.")
        columns = ["instrument", "dataset_family", "blocker", "recommended_action"]
        st.dataframe(critical[columns], hide_index=True, use_container_width=True)
