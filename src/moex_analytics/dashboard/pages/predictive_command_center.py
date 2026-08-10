"""Read-only visual command center for stored analog/fusion research."""

from __future__ import annotations

import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from moex_analytics.dashboard.data_access import read_connection

HORIZONS = (5, 20, 60, 120, 250)


def load_command_center(con) -> dict:
    """Read persisted research only; rendering never trains a model."""
    run = con.execute(
        "SELECT run_id FROM predictive_fusion_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not run:
        return {"ready": False}
    run_id = run[0]
    current = con.execute(
        "SELECT secid,horizon,signal,predicted_return,disagreement,abstained,"
        "abstention_reason,status FROM current_fusion_research WHERE run_id=? ORDER BY 1,2",
        [run_id],
    ).df()
    regime = con.execute(
        """SELECT regime,novelty_status,trade_date FROM regime_timeline_v2
           WHERE selected ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()
    return {"ready": True, "run_id": run_id, "current": current, "regime": regime}


def _heatmap(frame: pd.DataFrame) -> go.Figure:
    directions = {"positive": 1, "negative": -1, "unknown": 0}
    matrix = frame.assign(value=frame.signal.map(directions).fillna(0)).pivot(
        index="secid", columns="horizon", values="value"
    ).reindex(columns=list(HORIZONS))
    text = frame.assign(label=frame.apply(
        lambda row: "⚪ ?" if row.abstained else "🟢 ↑" if row.signal == "positive" else
        "🔴 ↓" if row.signal == "negative" else "🟡 →", axis=1
    )).pivot(index="secid", columns="horizon", values="label").reindex(columns=list(HORIZONS))
    return go.Figure(go.Heatmap(
        z=matrix, x=[f"{value}d" for value in matrix.columns], y=matrix.index,
        text=text, texttemplate="%{text}", zmin=-1, zmax=1,
        colorscale=[[0, "#dc3545"], [0.5, "#adb5bd"], [1, "#198754"]],
        colorbar={"title": "research direction"},
    )).update_layout(height=430, margin={"l": 20, "r": 20, "t": 20, "b": 20})


def _trajectory_chart(con, secid: str, method: str, window: int) -> go.Figure | None:
    run = con.execute(
        "SELECT run_id FROM analog_trajectory_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not run:
        return None
    frame = con.execute(
        """SELECT analog_date,forward_session,normalized_price FROM analog_forward_trajectories
           WHERE run_id=? AND secid=? AND method=? AND path_window=? AND forward_session<=250
           QUALIFY dense_rank() OVER (ORDER BY analog_date)<=20 ORDER BY analog_date,forward_session""",
        [run[0], secid, method, window],
    ).df()
    if frame.empty:
        return None
    figure = go.Figure()
    for date, path in frame.groupby("analog_date"):
        figure.add_scatter(x=path.forward_session, y=path.normalized_price, mode="lines",
                           line={"width": 1, "color": "rgba(80,120,180,.22)"},
                           name=str(date), showlegend=False)
    bands = (
        frame.groupby("forward_session").normalized_price
        .quantile([0.10, 0.25, 0.50, 0.75, 0.90]).unstack()
    )
    figure.add_scatter(x=bands.index, y=bands[0.90], line={"width": 0}, showlegend=False)
    figure.add_scatter(x=bands.index, y=bands[0.10], fill="tonexty",
                       fillcolor="rgba(70,130,180,.12)", line={"width": 0}, name="10–90%")
    figure.add_scatter(x=bands.index, y=bands[0.75], line={"width": 0}, showlegend=False)
    figure.add_scatter(x=bands.index, y=bands[0.25], fill="tonexty",
                       fillcolor="rgba(70,130,180,.25)", line={"width": 0}, name="25–75%")
    figure.add_scatter(x=bands.index, y=bands[0.50], line={"width": 4}, name="Медиана")
    figure.update_layout(title="Как развивались похожие исторические ситуации",
                         xaxis_title="Сессии после T0", yaxis_title="T0 = 100", height=520)
    return figure


def _top_analogs(con, secid: str, method: str, window: int) -> pd.DataFrame:
    return con.execute(
        """SELECT analog_date,episode_rank,round(similarity_score*100,1) similarity,
                  regime_agreement,event_state_agreement,why_similar_json,why_different_json
           FROM historical_analogs_v3 WHERE run_id=(SELECT run_id FROM analog_search_runs_v3
             WHERE status='completed' ORDER BY finished_at DESC LIMIT 1)
             AND analog_type='issuer' AND secid=? AND method=? AND path_window=?
           ORDER BY episode_rank LIMIT 10""",
        [secid, method, window],
    ).df()


def render_main() -> None:
    st.header("Прогноз рынка и моих акций")
    st.caption("Research/shadow evidence. Это не обещание доходности и не production-сигнал.")
    with read_connection() as con:
        try:
            data = load_command_center(con)
        except Exception:
            st.info("Расчёт predictive command center ещё не готов.")
            return
        if not data["ready"]:
            st.info("Сначала выполните полный research-расчёт исторических аналогов.")
            return
        regime = data["regime"]
        cols = st.columns(3)
        cols[0].metric("Режим рынка", f"Режим {regime[0]}" if regime else "Недостаточно данных")
        cols[1].metric("Историческая знакомость", regime[1] if regime else "unknown")
        cols[2].metric("Статус", "Shadow / abstain")
        st.subheader("Карта портфельных горизонтов")
        st.plotly_chart(_heatmap(data["current"]), use_container_width=True)
        st.info("⚪ означает abstention: исследовательские данные есть, но доказательности недостаточно.")
        secid = st.selectbox("Бумага", sorted(data["current"].secid.unique()), key="command_stock")
        selected = data["current"].loc[data["current"].secid == secid]
        st.dataframe(selected, use_container_width=True, hide_index=True)
        figure = _trajectory_chart(con, secid, "robust_euclidean", 0)
        if figure:
            st.plotly_chart(figure, use_container_width=True)


def render_explorer() -> None:
    st.header("Historical Analog Explorer 3.0")
    st.caption("Все фильтры читают сохранённые результаты; model training при отрисовке не запускается.")
    with read_connection() as con:
        instruments = [row[0] for row in con.execute(
            "SELECT DISTINCT secid FROM historical_analogs_v3 WHERE analog_type='issuer' ORDER BY 1"
        ).fetchall()]
        if not instruments:
            st.info("Исторические аналоги ещё не рассчитаны.")
            return
        secid = st.selectbox("Инструмент", instruments, key="analog_explorer_stock")
        methods = con.execute(
            "SELECT DISTINCT method,path_window FROM historical_analogs_v3 "
            "WHERE analog_type='issuer' AND secid=? ORDER BY 1,2", [secid]
        ).fetchall()
        label = st.selectbox("Метод", [f"{method} / {window}" for method, window in methods])
        method, window = label.split(" / ")
        window = int(window)
        figure = _trajectory_chart(con, secid, method, window)
        if figure:
            st.plotly_chart(figure, use_container_width=True)
        top = _top_analogs(con, secid, method, window)
        st.subheader("Лучшие исторические эпизоды")
        for row in top.itertuples():
            with st.expander(f"#{row.episode_rank} — {row.analog_date} · similarity {row.similarity}/100"):
                st.write("Главные сходства:", json.loads(row.why_similar_json or "{}"))
                st.write("Главные различия:", json.loads(row.why_different_json or "{}"))
                st.write("Совпадение режима:", row.regime_agreement,
                         "Совпадение event-state:", row.event_state_agreement)
        distributions = con.execute(
            """SELECT horizon,effective_n,median_return,q10,q25,q75,q90,positive_fraction,
                      mean_adverse_excursion,mean_favorable_excursion,status
               FROM analog_terminal_distributions WHERE secid=? AND method=? AND path_window=?
               ORDER BY horizon""", [secid, method, window]
        ).df()
        st.subheader("Распределение фактических исходов")
        st.dataframe(distributions, use_container_width=True, hide_index=True)
        if not distributions.empty:
            chart = px.bar(distributions, x="horizon", y="median_return",
                           error_y=distributions.q75 - distributions.median_return,
                           error_y_minus=distributions.median_return - distributions.q25,
                           title="Медиана и исторический межквартильный диапазон")
            st.plotly_chart(chart, use_container_width=True)
