"""Controlled self-learning BASIC and advanced views."""

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection, table_exists
from moex_analytics.learning_cycle import learning_status


def _data(con, run_id):
    totals = con.execute(
        """SELECT
        (SELECT count(*) FROM canonical_daily_prices) observations,
        (SELECT count(*) FROM tournament_results) models,
        (SELECT count(*) FROM forecast_registry) forecasts,
        (SELECT count(*) FROM forecast_outcomes WHERE outcome_status='matured') matured,
        (SELECT count(*) FROM learning_model_versions WHERE status='shadow') shadow,
        (SELECT count(*) FROM probability_calibration_audit WHERE probability_allowed) probabilities"""
    ).fetchone()
    cards = con.execute(
        "SELECT * FROM model_champion_table WHERE run_id=? ORDER BY secid,horizon", [run_id]
    ).df()
    return totals, cards


def render_basic() -> None:
    st.header("Обучение системы")
    with read_connection() as con:
        status = learning_status(con, ensure=False)
        if not status["latest"]:
            st.info("Полный контролируемый цикл обучения ещё не запускался.")
            return
        totals, cards = _data(con, status["latest"][0])
        quality = None
        if table_exists(con, "historical_quality_v2"):
            eligible = con.execute(
                "SELECT count(*) FROM historical_quality_v2 WHERE training_tier IN ('A','B')"
            ).fetchone()[0]
            historical = con.execute("SELECT count(DISTINCT secid) FROM moex_equity_eod").fetchone()[0]
            confirmed = con.execute(
                """SELECT count(*) FROM clean_relearning_results WHERE status IN
                ('IMPROVED_BY_CLEAN_DATA','SHADOW_CANDIDATE') AND run_id=(SELECT run_id
                FROM clean_relearning_runs WHERE status='completed' ORDER BY started_at DESC LIMIT 1)"""
            ).fetchone()[0]
            quality = (
                "хорошее" if eligible >= 200 else "среднее" if eligible >= 100 else "низкое",
                historical,
                eligible,
                confirmed,
            )
    columns = st.columns(6)
    labels = ("Наблюдения", "Модели", "Live forecasts", "Matured", "Shadow", "Probability approved")
    for column, label, value in zip(columns, labels, totals, strict=True):
        column.metric(label, value)
    st.subheader("Накопление проверяемых данных")
    progress_items = (
        ("Исторические наблюдения", totals[0]),
        ("Проверенные модели", totals[1]),
        ("Сохранённые live-прогнозы", totals[2]),
        ("Созревшие исходы", totals[3]),
        ("Shadow-модели", totals[4]),
        ("Разрешённые вероятности", totals[5]),
    )
    for label, value in progress_items:
        st.write(f"**{label}: {value}**")
    st.caption("Это абсолютные объёмы, не процент готовности и не обещание качества модели.")
    if totals[3] == 0:
        st.info("Live-обучение только началось.")
    if quality:
        st.write(f"Качество обучающей базы: **{quality[0]}**")
        st.write(
            f"Исторических бумаг: **{quality[1]}** · Пригодных для обучения: **{quality[2]}** · "
            f"Подтверждённых predictive combinations: **{quality[3]}**"
        )
    st.dataframe(
        cards[["secid", "horizon", "current_champion", "best_challenger", "live_n", "status"]],
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Автоматический production promotion запрещён; возможен только manual review.")


def render_advanced() -> None:
    st.header("Controlled Self-Learning Loop")
    with read_connection() as con:
        status = learning_status(con, ensure=False)
        if not status["latest"]:
            st.info("Learning cycle ещё не запускался.")
            return
        run_id = status["latest"][0]
        checkpoints = con.execute(
            "SELECT * FROM learning_cycle_checkpoints WHERE run_id=? ORDER BY stage", [run_id]
        ).df()
        reviews = con.execute(
            "SELECT * FROM learning_promotion_review WHERE run_id=? ORDER BY secid,horizon", [run_id]
        ).df()
    st.dataframe(checkpoints, use_container_width=True, hide_index=True)
    st.subheader("Manual promotion review")
    st.dataframe(reviews, use_container_width=True, hide_index=True)
    st.warning("Eligible for review не означает auto-promote.")
