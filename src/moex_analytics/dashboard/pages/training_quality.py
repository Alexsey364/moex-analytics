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


def render_training_universe() -> None:
    st.header("Обучающая выборка")
    with read_connection() as con:
        if not table_exists(con, "training_universe_runs"):
            st.info("Stage 32 ещё не рассчитан.")
            return
        row = con.execute(
            """SELECT raw_securities,eligible_securities,rows,dates,dataset_version,cutoff
            FROM training_universe_runs ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        cols = st.columns(4)
        cols[0].metric("Raw universe", row[0])
        cols[1].metric("Training universe", row[1])
        cols[2].metric("Rows", row[2])
        cols[3].metric("Dates", row[3])
        st.caption(f"Frozen dataset {row[4]}, cutoff {row[5]}")
        st.dataframe(
            con.execute(
                """SELECT quality_tier,count(DISTINCT secid) securities,count(*) rows
                FROM historical_training_panel WHERE dataset_version=? GROUP BY 1 ORDER BY 1""",
                [row[4]],
            ).df(),
            use_container_width=True,
        )
