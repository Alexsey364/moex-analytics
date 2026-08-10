"""Research-only model tournament views."""

import plotly.graph_objects as go
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
    with read_connection() as con:
        evidence = con.execute(
            "SELECT secid,horizon,model,advantage,ci_low,ci_high,status FROM tournament_results "
            "WHERE run_id=? AND split='test' ORDER BY secid,horizon,advantage DESC", [run_id]
        ).df()
    if not evidence.empty:
        secid = st.selectbox("Бумага", sorted(evidence.secid.unique()), key="tournament_secid")
        horizon = st.selectbox(
            "Горизонт", sorted(evidence[evidence.secid == secid].horizon.unique()),
            key="tournament_horizon",
        )
        selected = evidence[(evidence.secid == secid) & (evidence.horizon == horizon)].copy()
        selected["weak"] = (selected.ci_low <= 0) & (selected.ci_high >= 0)
        error_plus = selected.ci_high - selected.advantage
        error_minus = selected.advantage - selected.ci_low
        figure = go.Figure(go.Bar(
            x=selected.model, y=selected.advantage,
            marker_color=selected.weak.map({True: "#8c959f", False: "#0969da"}),
            error_y=dict(type="data", array=error_plus, arrayminus=error_minus),
            customdata=selected[["status", "weak"]],
            hovertemplate="%{x}<br>OOS improvement %{y:.3f}<br>Status %{customdata[0]}"
            "<br>CI пересекает 0: %{customdata[1]}<extra></extra>",
        ))
        figure.add_hline(y=0, line_dash="dash")
        figure.update_layout(title="OOS improvement с confidence interval", yaxis_title="Advantage")
        st.plotly_chart(figure, use_container_width=True)
        st.caption("Серый: confidence interval пересекает ноль — evidence слабое.")
    if rows and all(row["Победитель"] == "unconditional" for row in rows):
        st.warning("Ни одна модель пока надёжно не превосходит простой baseline.")
    else:
        st.info("Лучшая исследовательская модель показана отдельно для каждого горизонта.")
    st.caption("Результаты research-only. Автоматическое изменение production запрещено.")
