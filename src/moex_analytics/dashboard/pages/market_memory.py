"""Historical market memory dashboard."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.dashboard.human_experience import human_status
from moex_analytics.market_memory import market_memory_status


def render() -> None:
    st.header("Историческая память рынка")
    with read_connection() as con:
        status = market_memory_status(con, ensure=False)
        if not status["latest"]:
            st.info("Исследование исторических аналогов ещё не запускалось.")
            return
        run_id = status["latest"][0]
        instruments = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT instrument FROM market_analog_scorecards WHERE run_id=? ORDER BY 1", [run_id]
            ).fetchall()
        ]
        instrument = st.selectbox("Инструмент", instruments)
        horizon = st.selectbox("Горизонт", [5, 20, 60, 120])
        frame = con.execute(
            """SELECT method,cutoff_date,sample,similarity,median_return,q10,q90,
            positive_fraction,median_drawdown,median_mfe,oos_value_add,status,reason
            FROM market_analog_scorecards WHERE run_id=? AND instrument=? AND horizon=?""",
            [run_id, instrument, horizon],
        ).df()
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption("Аналоги — research-only challenger evidence; production decision не меняется.")


def render_basic_analogs(instrument: str) -> None:
    """Render compact analog evidence for the selected BASIC stock card."""
    with read_connection() as con:
        status = market_memory_status(con, ensure=False)
        if not status["latest"]:
            return
        rows = con.execute(
            """SELECT horizon,sample,similarity,median_return,q10,q90,status
            FROM market_analog_scorecards WHERE run_id=? AND instrument=?
            QUALIFY row_number() OVER (
              PARTITION BY horizon ORDER BY oos_value_add DESC NULLS LAST
            )=1 ORDER BY horizon""",
            [status["latest"][0], instrument],
        ).fetchall()
    if not rows:
        st.info("Исторических аналогов для этой бумаги недостаточно.")
        return
    st.subheader("На какие периоды это похоже")
    horizon_names = {5: "1 неделя", 20: "1 месяц", 60: "3 месяца", 120: "6 месяцев", 250: "1 год"}
    for horizon, sample, _similarity, median, q10, q90, result_status in rows:
        if sample < 8:
            st.write(f"{horizon_names.get(horizon, str(horizon))}: пока недостаточно похожих эпизодов.")
            continue
        st.write(
            f"{horizon_names.get(horizon, str(horizon))}: найдено {sample} похожих эпизодов; "
            f"типичный результат {median:+.1%}, исторический диапазон {q10:+.1%}…{q90:+.1%}. "
            f"{human_status(result_status)}"
        )
