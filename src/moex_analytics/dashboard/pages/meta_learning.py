"""Abstention and meta confidence dashboard."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.meta_learning import meta_learning_status


def render() -> None:
    st.header("Когда модели лучше промолчать")
    with read_connection() as con:
        status = meta_learning_status(con, ensure=False)
        if not status["latest"]:
            st.info("Meta-learning ещё не запускался.")
            return
        run_id = status["latest"][0]
        cards = con.execute(
            "SELECT * FROM meta_confidence_scorecards WHERE run_id=? ORDER BY secid,horizon",
            [run_id],
        ).df()
        curves = con.execute(
            """SELECT * FROM selective_accuracy_curves WHERE run_id=?
            ORDER BY secid,horizon,target_coverage DESC""",
            [run_id],
        ).df()
    st.dataframe(cards, use_container_width=True, hide_index=True)
    with st.expander("Selective accuracy curve"):
        st.dataframe(curves, use_container_width=True, hide_index=True)
    st.caption("Пороги зафиксированы на train-периоде; production-модель не меняется.")
