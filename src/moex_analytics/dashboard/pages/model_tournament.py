"""Research-only model tournament views."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.model_tournament import tournament_status


def render() -> None:
    st.header("Турнир моделей")
    with read_connection() as con:
        status = tournament_status(con, ensure=False)
    if not status["latest"]:
        st.info("Турнир ещё не запускался.")
        return
    run_id, state, runtime, models, folds = status["latest"]
    st.caption(f"Run {run_id} · {state} · {runtime:.1f} сек.")
    left, right = st.columns(2)
    left.metric("Моделей проверено", models)
    right.metric("Walk-forward fold", folds)
    rows = [
        {
            "Бумага": secid,
            "Горизонт": horizon,
            "Победитель": winner,
            "Статус": result,
            "Причина": reason,
        }
        for secid, horizon, winner, result, reason in status["leaderboard"]
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if rows and all(row["Победитель"] == "unconditional" for row in rows):
        st.warning("Ни одна модель пока надёжно не превосходит простой baseline.")
    else:
        st.info("Лучшая исследовательская модель показана отдельно для каждого горизонта.")
    st.caption("Результаты research-only. Автоматическое изменение production запрещено.")
