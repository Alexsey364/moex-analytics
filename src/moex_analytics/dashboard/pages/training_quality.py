"""Stage 31-33 data-quality research pages."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection, table_exists


def render_corporate_actions() -> None:
    st.header("Корпоративные действия и качество цен")
    with read_connection() as con:
        if not table_exists(con, "corporate_action_candidate_episodes"):
            st.info("Stage 31 ещё не рассчитан.")
            return
        totals = con.execute(
            """SELECT count(*),count(*) FILTER(review_status='auto_validated'),
            count(*) FILTER(review_status='manual_review_required'),
            count(*) FILTER(review_status='unresolved')
            FROM corporate_action_candidate_episodes"""
        ).fetchone()
        cols = st.columns(4)
        for col, label, value in zip(
            cols, ("Episodes", "Подтверждено", "Manual review", "Unresolved"), totals, strict=True
        ):
            col.metric(label, value)
        st.dataframe(
            con.execute(
                """SELECT secid,priority,count(*) episodes,
                count(*) FILTER(review_status='auto_validated') resolved,
                count(*) FILTER(review_status='manual_review_required') manual_review,
                count(*) FILTER(review_status='unresolved') unresolved
                FROM corporate_action_candidate_episodes GROUP BY 1,2 ORDER BY 2,3 DESC"""
            ).df(),
            use_container_width=True,
        )
        st.caption("Raw EOD не изменяется. Ratio detection не является подтверждением события.")
