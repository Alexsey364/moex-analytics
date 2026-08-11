"""Read-only visual command center for stored analog/fusion research."""

from __future__ import annotations

import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.dashboard.visual_semantics import color_for

HORIZONS = (5, 20, 60, 120, 250)


def load_visual_lab(con) -> dict:
    """Load immutable Stage 52-58 results without calculating or fitting anything."""
    ranking = con.execute(
        "SELECT * FROM current_portfolio_ranking WHERE run_id=(SELECT run_id FROM "
        "ranking_research_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1)"
    ).df()
    distributions = con.execute(
        "SELECT * FROM current_return_distributions WHERE run_id=(SELECT run_id FROM "
        "distribution_research_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1)"
    ).df()
    opportunity = con.execute(
        "SELECT * FROM opportunity_candidates WHERE run_id=(SELECT run_id FROM "
        "opportunity_research_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1) "
        "AND candidate_type='equity'"
    ).df()
    plans = con.execute(
        "SELECT * FROM portfolio_allocation_plans WHERE run_id=(SELECT run_id FROM "
        "cash_aware_optimizer_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1)"
    ).df()
    try:
        freshness = con.execute(
            "SELECT cutoff,status FROM snapshot_freshness_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        freshness = None
    return {
        "ranking": ranking,
        "distributions": distributions,
        "opportunity": opportunity,
        "plans": plans,
        "ready": not ranking.empty,
        "freshness_warning": freshness if freshness and freshness[1] != "complete" else None,
    }


def evidence_label(status: object) -> str:
    value = str(status or "unknown").upper()
    if value in {"ROBUST", "PASS", "PRODUCTION_CANDIDATE"}:
        return "🟢 подтверждено"
    if value in {"CONDITIONAL", "EXPERIMENTAL", "SHADOW"}:
        return "🟡 исследуется"
    if value in {"REJECTED", "FAIL"}:
        return "🔴 отклонено"
    return "⚪ недостаточно данных"


def _ranking_board(frame: pd.DataFrame) -> go.Figure:
    latest = frame.loc[frame.horizon == 60].sort_values("relative_rank")
    return px.bar(
        latest,
        x="relative_rank",
        y="secid",
        orientation="h",
        error_x=latest.rank_high - latest.relative_rank,
        error_x_minus=latest.relative_rank - latest.rank_low,
        color="tie_group",
        title="Относительное место на горизонте 60 сессий (перекрытия = одна группа)",
        labels={"relative_rank": "percentile rank", "secid": "бумага"},
    )


def _term_structure(frame: pd.DataFrame, secid: str) -> go.Figure:
    selected = frame.loc[frame.secid == secid].sort_values("horizon")
    figure = go.Figure()
    figure.add_scatter(x=selected.horizon, y=selected.q90_return, line={"width": 0})
    figure.add_scatter(
        x=selected.horizon, y=selected.q10_return, fill="tonexty",
        fillcolor="rgba(88,166,255,.15)", line={"width": 0}, name="10–90%",
    )
    figure.add_scatter(x=selected.horizon, y=selected.q75_return, line={"width": 0})
    figure.add_scatter(
        x=selected.horizon, y=selected.q25_return, fill="tonexty",
        fillcolor="rgba(88,166,255,.30)", line={"width": 0}, name="25–75%",
    )
    figure.add_scatter(
        x=selected.horizon, y=selected.q50_return, mode="lines+markers", name="Медиана"
    )
    return figure.update_layout(
        title="Структура исторического диапазона по горизонтам",
        xaxis_title="торговые сессии", yaxis_title="доходность", height=420,
    )


def _opportunity_scatter(frame: pd.DataFrame) -> go.Figure:
    selected = frame.loc[frame.horizon == 60].copy()
    selected["visual_status"] = selected.apply(
        lambda row: "insufficient" if row.abstain else row.evidence_quality, axis=1
    )
    colors = {value: color_for(value) for value in selected.visual_status.unique()}
    return px.scatter(
        selected, x="downside_axis", y="opportunity_axis", text="secid",
        size=selected.portfolio_weight.fillna(0).clip(lower=.01), color="visual_status",
        color_discrete_map=colors, hover_data=["timing_status", "quadrant", "abstention_reason"],
        title="Возможность и downside: выше — интереснее, правее — больше риск",
    ).update_traces(textposition="top center")


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
        st.divider()
        try:
            lab = load_visual_lab(con)
        except Exception:
            st.info("Visual Forecast Lab появится после завершённого research-цикла.")
            return
        if not lab["ready"]:
            return
        if lab["freshness_warning"]:
            st.warning(
                "Свежая predictive-выборка неполна и не смешивается со старой. "
                "Показан последний полный immutable snapshot по девяти бумагам."
            )
        st.subheader("Сравнение моих бумаг")
        st.plotly_chart(_ranking_board(lab["ranking"]), use_container_width=True)
        st.caption(
            "Близкие интервалы рангов образуют overlap-группу: порядок внутри неё не доказан."
        )
        st.plotly_chart(_opportunity_scatter(lab["opportunity"]), use_container_width=True)
        selected_stock = st.selectbox(
            "Диапазоны бумаги", sorted(lab["distributions"].secid.unique()), key="lab_stock"
        )
        st.plotly_chart(
            _term_structure(lab["distributions"], selected_stock), use_container_width=True
        )
        st.warning(
            "Диапазоны — фактическое распределение OOS-ошибок исследовательского метода, "
            "а не разрешённая числовая вероятность роста. Probability gate не ослаблен."
        )


def render_opportunity() -> None:
    st.header("Opportunity Map")
    st.caption("Только сохранённые Stage 56 результаты; расчёта при открытии страницы нет.")
    try:
        with read_connection() as con:
            lab = load_visual_lab(con)
            distilled = con.execute(
                "SELECT secid,status_code,group_60,group_120,group_250,portfolio_fit,data_quality "
                "FROM distilled_investor_views WHERE run_id=(SELECT run_id FROM "
                "investor_decision_runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1)"
            ).df()
    except Exception:
        st.info("Нет полного актуального opportunity snapshot. Запустите обновление данных.")
        return
    if lab["freshness_warning"]:
        st.warning("Текущий universe неполон. Старые и новые snapshots не смешиваются.")
        return
    if not distilled.empty:
        lab["opportunity"] = lab["opportunity"].merge(distilled, on="secid", how="left")
        lab["opportunity"]["visual_status"] = lab["opportunity"].status_code.fillna("GRAY")
    else:
        lab["opportunity"]["visual_status"] = "GRAY"
    with st.container():
        if lab["opportunity"].empty:
            st.info("Нет полного актуального opportunity snapshot. Запустите обновление данных.")
            return
        selected = lab["opportunity"].loc[lab["opportunity"].horizon == 60].copy()
        colors = {value: color_for(value) for value in selected.visual_status.unique()}
        figure = px.scatter(
            selected, x="downside_axis", y="opportunity_axis", text="secid",
            size=selected.portfolio_weight.fillna(0).clip(lower=.01), color="visual_status",
            color_discrete_map=colors,
            hover_data=["relative_rank", "group_60", "group_120", "group_250",
                        "expected_median", "tail_downside", "data_quality", "portfolio_fit"],
            title="Возможность и downside: выше — интереснее, правее — больше риск",
        ).update_traces(textposition="top center")
        st.plotly_chart(figure, use_container_width=True)
        visible = lab["opportunity"][[
            "secid", "horizon", "quadrant", "timing_status", "evidence_quality",
            "abstain", "abstention_reason",
        ]].copy()
        visible["доказательность"] = visible.evidence_quality.map(evidence_label)
        st.dataframe(visible, use_container_width=True, hide_index=True)


def render_optimizer() -> None:
    st.header("Cash-aware Portfolio Optimizer")
    st.caption("Research only: план не создаёт заявки и может оставить всю сумму в резерве.")
    with read_connection() as con:
        lab = load_visual_lab(con)
        if lab["plans"].empty:
            st.info("Сначала выполните cash-aware optimizer.")
            return
        tranche = st.selectbox(
            "Сумма пополнения", sorted(lab["plans"].tranche.unique()), format_func=lambda x: f"{x:,.0f} ₽"
        )
        plans = lab["plans"].loc[lab["plans"].tranche == tranche].sort_values("plan_rank")
        winner = plans.iloc[0]
        columns = st.columns(3)
        columns[0].metric("Распределить", f"{winner.invested:,.0f} ₽")
        columns[1].metric("Оставить в резерве", f"{winner.cash_reserve:,.0f} ₽")
        columns[2].metric(
            "Статус",
            "🔵 CASH предпочтительнее"
            if winner.status == "CASH_PREFERRED"
            else evidence_label(winner.robustness),
        )
        st.json(json.loads(winner.allocation_json))
        st.info(
            "Сейчас CASH выигрывает: рискованные варианты не прошли frozen OOS-критерии. "
            "Ни один runner-up не является рекомендацией."
        )
        st.subheader("Сравнимые варианты")
        st.dataframe(
            plans[["plan_rank", "allocation_json", "invested", "cash_reserve", "robustness", "status"]],
            use_container_width=True, hide_index=True,
        )


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
def render_live_ranking():
    """Show prospective ranking evidence without implying matured observations."""
    st.header("Live ranking snapshots")
    try:
        with read_connection() as con:
            totals = con.execute(
                "SELECT count(*) total,count(*) FILTER(WHERE status='pending') pending,"
                "count(*) FILTER(WHERE status='matured') matured FROM live_ranking_outcomes"
            ).df().iloc[0]
    except Exception:
        st.info("Недостаточно live-данных: наблюдение ещё не начато.")
        return
    columns = st.columns(3)
    columns[0].metric("Всего snapshots", int(totals.total))
    columns[1].metric("Ожидают maturity", int(totals.pending))
    columns[2].metric("Созрели", int(totals.matured))
    if int(totals.matured) < 30:
        st.info("Недостаточно live evidence. История не реконструируется задним числом.")


def render_distilled():
    """Render only saved Stage 65 evidence; never calculate or infer in UI."""
    st.header("Что сейчас выглядит лучше остальных")
    try:
        with read_connection() as con:
            frame = con.execute(
                "SELECT secid AS Ticker,status_label AS Status,group_60 AS horizon_60,"
                "group_120 AS horizon_120,group_250 AS horizon_250,downside AS Downside,"
                "analog_role,timing,portfolio_fit,data_quality AS Evidence,live_n FROM "
                "distilled_investor_views WHERE run_id=(SELECT run_id FROM investor_decision_runs "
                "WHERE status='completed' ORDER BY created_at DESC LIMIT 1) ORDER BY Ticker"
            ).df()
    except Exception:
        st.info("Недостаточно сохранённых данных для итогового представления.")
        return
    if frame.empty:
        st.info("Недостаточно сохранённых данных для итогового представления.")
        return
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption("Ранги относительные, не обещают рост. Числовая вероятность не публикуется.")
    if (frame["live_n"] < 30).all():
        st.warning("Live evidence пока недостаточно; optimizer сохраняет CASH_PREFERRED.")
