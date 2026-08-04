import streamlit as st

from ..data_access import database_tables, instrument_summary


def render():
    st.header("Состояние базы")
    stats = instrument_summary()
    st.subheader("Инструменты")
    st.dataframe(stats, use_container_width=True, hide_index=True)
    st.download_button(
        "Экспорт статистики в CSV",
        stats.to_csv(index=False).encode("utf-8-sig"),
        "moex_database_status.csv",
        "text/csv",
    )
    st.subheader("Таблицы DuckDB")
    st.dataframe(database_tables(), use_container_width=True, hide_index=True)
