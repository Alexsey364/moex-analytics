"""Research-only predictive learning dashboard."""

import streamlit as st

from moex_analytics.adaptive_learning.core import ensure_schema
from moex_analytics.database import connection


def _query(sql, params=None):
    with connection(read_only=False) as con:
        ensure_schema(con)
        return con.execute(sql, params or []).df()


def render_advanced() -> None:
    st.header("Обучение моделей")
    runs = _query("SELECT * FROM adaptive_research_runs ORDER BY created_at DESC")
    if runs.empty:
        st.info("Research-запуск ещё не выполнен. Production-модель не изменяется.")
        return
    st.dataframe(runs, use_container_width=True, hide_index=True)
    instruments = _query("SELECT DISTINCT secid FROM adaptive_model_leaderboard ORDER BY secid")
    secid = st.selectbox("Инструмент", instruments.secid.tolist())
    horizon = st.selectbox("Горизонт", [5, 20, 60, 120])
    board = _query(
        """SELECT model,scope,observations,folds,balanced_accuracy,roc_auc,brier,
        delta_balanced_accuracy,return_mae,coverage_50,coverage_80,coverage_90,
        calibration_slope,ece,regime_stability,fold_wins,probability_allowed,
        confidence,status FROM adaptive_model_leaderboard
        WHERE secid=? AND horizon=? ORDER BY delta_balanced_accuracy DESC""",
        [secid, horizon],
    )
    st.dataframe(board, use_container_width=True, hide_index=True)
    tabs = st.tabs(["Feature importance", "Ablation", "Promotion review", "Ranking"])
    with tabs[0]:
        st.dataframe(
            _query(
                "SELECT * FROM adaptive_feature_importance WHERE secid=? AND horizon=? "
                "ORDER BY importance DESC",
                [secid, horizon],
            ),
            use_container_width=True,
            hide_index=True,
        )
    with tabs[1]:
        st.dataframe(
            _query("SELECT * FROM adaptive_feature_ablation WHERE secid=? AND horizon=?", [secid, horizon]),
            use_container_width=True,
            hide_index=True,
        )
    with tabs[2]:
        st.dataframe(
            _query("SELECT * FROM adaptive_promotion_review WHERE secid=? AND horizon=?", [secid, horizon]),
            use_container_width=True,
            hide_index=True,
        )
    with tabs[3]:
        st.dataframe(
            _query("SELECT * FROM adaptive_ranking_results ORDER BY horizon,scope"),
            use_container_width=True,
            hide_index=True,
        )
    st.warning("Research-only: ни одна запись не меняет production автоматически.")


def render_basic() -> None:
    st.header("Как программа учится")
    latest = _query(
        "SELECT run_id,status,created_at,models_trained,folds FROM adaptive_research_runs "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if latest.empty:
        st.info(
            "Исследовательские модели ещё не рассчитаны. "
            "Рабочая production-модель не переобучается ежедневно."
        )
        return
    st.dataframe(latest, use_container_width=True, hide_index=True)
    cards = _query("""SELECT secid,horizon,model,observations,delta_balanced_accuracy,
        probability_allowed,confidence,status FROM adaptive_model_leaderboard
        QUALIFY row_number() over(PARTITION BY secid,horizon ORDER BY delta_balanced_accuracy DESC)=1
        ORDER BY secid,horizon""")
    for row in cards.itertuples():
        label = (
            "кандидат подтверждается"
            if row.status == "candidate"
            else "качество ухудшилось"
            if row.status == "rejected"
            else "модель ещё учится"
        )
        st.subheader(f"{row.secid} / {row.horizon} сессий")
        st.write(
            f"Историческая OOS-проверка: {row.observations} наблюдений; "
            f"преимущество к baseline {row.delta_balanced_accuracy:+.3f}."
        )
        st.write(f"Уверенность evidence: {row.confidence:.0%}. {label}.")
        if not row.probability_allowed:
            st.caption("Числовая вероятность скрыта: calibration/OOS policy не выполнена.")
