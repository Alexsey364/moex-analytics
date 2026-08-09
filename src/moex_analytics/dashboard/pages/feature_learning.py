"""Dynamic feature memory views."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.feature_learning import feature_learning_status


def render() -> None:
    st.header("Динамическая полезность факторов")
    with read_connection() as con:
        status = feature_learning_status(con, ensure=False)
        if not status["latest"]:
            st.info("Feature learning ещё не запускался.")
            return
        run_id = status["latest"][0]
        instruments = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT instrument FROM feature_dynamic_scorecards WHERE run_id=? ORDER BY 1",
                [run_id],
            ).fetchall()
        ]
        instrument = st.selectbox("Инструмент", instruments)
        horizon = st.selectbox("Горизонт", [5, 20, 60, 120])
        frame = con.execute(
            """SELECT feature,family,long_run_ic,recent_ic,shrunk_ic,fold_stability,
            regimes_worked,sign_changes,sample,status,reason FROM feature_dynamic_scorecards
            WHERE run_id=? AND instrument=? AND horizon=?
            ORDER BY abs(shrunk_ic) DESC NULLS LAST""",
            [run_id, instrument, horizon],
        ).df()
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption("Recent evidence shrinked к long-run истории; production weights не меняются.")


def render_basic_scorecards() -> None:
    """Show compact, evidence-backed feature memory on the BASIC learning page."""
    with read_connection() as con:
        status = feature_learning_status(con, ensure=False)
        if not status["latest"]:
            st.info("История полезности факторов ещё не рассчитана.")
            return
        run_id = status["latest"][0]
        rows = con.execute(
            """SELECT instrument,horizon,feature,family,shrunk_ic,status
            FROM feature_dynamic_scorecards WHERE run_id=?
            QUALIFY row_number() OVER (
              PARTITION BY instrument,horizon ORDER BY abs(shrunk_ic) DESC NULLS LAST
            ) <= 3
            ORDER BY instrument,horizon,abs(shrunk_ic) DESC NULLS LAST""",
            [run_id],
        ).fetchall()
    st.subheader("Что исторически помогает прогнозировать бумаги")
    icons = {
        "stable_positive": "🟢",
        "stable_negative": "🟢",
        "regime_dependent": "🟡",
        "decaying": "🟠",
        "sign_flip": "🔴",
        "noise": "🔴",
        "insufficient_sample": "⚪",
    }
    for instrument, horizon, feature, family, value, feature_status in rows:
        score = "нет оценки" if value is None else f"shrunk IC {value:+.3f}"
        st.write(
            f"{icons.get(feature_status, '⚪')} {instrument} / {horizon}: "
            f"{feature} ({family}) — {feature_status}, {score}."
        )
    st.caption("Недавние результаты сжаты к длинной истории; веса production не меняются.")
