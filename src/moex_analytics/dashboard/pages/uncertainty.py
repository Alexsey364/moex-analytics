"""Calibration and uncertainty research dashboard."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.uncertainty import calibration_status


def render() -> None:
    st.header("Калибровка и неопределённость")
    with read_connection() as con:
        status = calibration_status(con, ensure=False)
        if not status["latest"]:
            st.info("Temporal calibration audit ещё не выполнялся.")
            return
        run_id = status["latest"][0]
        summary = con.execute(
            """SELECT secid,horizon,model,method,test_n,auc,brier,baseline_brier,ece,
            slope,intercept,probability_allowed,status,reason
            FROM probability_calibration_audit WHERE run_id=?
            ORDER BY probability_allowed DESC,secid,horizon,model,method""",
            [run_id],
        ).df()
        intervals = con.execute(
            "SELECT * FROM prediction_interval_audit WHERE run_id=? ORDER BY secid,horizon,model",
            [run_id],
        ).df()
    st.metric("Probability approved", int(summary.probability_allowed.sum()))
    st.dataframe(summary, use_container_width=True, hide_index=True)
    with st.expander("Prediction intervals: empirical coverage"):
        st.dataframe(intervals, use_container_width=True, hide_index=True)
    st.warning("Probability скрыта для каждой строки, не прошедшей все обязательные gates.")
