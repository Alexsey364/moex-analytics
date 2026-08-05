"""Run with: python -m moex_analytics.dashboard.app."""

import subprocess
import sys

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

if get_script_run_ctx() is None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            __file__,
            "--server.address",
            "localhost",
            "--server.port",
            "8501",
        ],
        check=True,
    )
    raise SystemExit

from moex_analytics.dashboard.data_access import DatabaseUnavailable, database_summary
from moex_analytics.dashboard.pages import (
    analytics,
    data_quality,
    database_status,
    instrument,
    methodology,
    overview,
    update_data,
)
from moex_analytics.database import database_path, init_database

st.set_page_config(page_title="Аналитика рынка MOEX", layout="wide")
st.title("Аналитика рынка MOEX")

if not database_path().exists():
    st.warning("База данных ещё не создана.")
    if st.button("Создать пустую базу"):
        init_database()
        st.success("База создана. Откройте страницу «Обновление данных».")
        st.rerun()
    st.stop()

try:
    summary = database_summary()
except DatabaseUnavailable as exc:
    st.error(str(exc))
    st.info("Закройте другую операцию с базой и обновите страницу.")
    st.stop()

if not summary.get("ready"):
    st.warning("База создана, но схема неполная. Выполните начальную настройку.")

top = st.columns(4)
top[0].metric("Состояние базы", "Готова" if summary.get("ready") else "Настройка")
top[1].metric("Последнее обновление", str(summary.get("last_load") or "—"))
top[2].metric("Проблем качества", summary.get("issues", 0))
top[3].metric(
    "Диапазон",
    f"{summary.get('date_from', '—')} — {summary.get('date_to', '—')}",
)

pages = {
    "Аналитика инструмента": analytics.render_summary,
    "Факторы": analytics.render_factors,
    "Исторические аналоги": analytics.render_analogues,
    "Режим рынка": analytics.render_regimes,
    "Проверка модели": analytics.render_validation,
    "Обзор": overview.render,
    "Инструмент": instrument.render,
    "Качество данных": data_quality.render,
    "Состояние базы": database_status.render,
    "Обновление данных": update_data.render,
    "Методология": methodology.render,
}
selected = st.sidebar.radio("Навигация", pages)
pages[selected]()
