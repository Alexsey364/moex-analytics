"""Forecast versus fact and live track-record dashboard pages."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from moex_analytics.database import connection
from moex_analytics.portfolio_research.forecast_scorecards import forecast_status


def _q(sql, params=None):
    try:
        with connection(read_only=True) as con:
            return con.execute(sql, params or []).df()
    except Exception:
        return pd.DataFrame()


def render_basic():
    st.header("Как программа прогнозирует")
    try:
        with connection() as con:
            status = forecast_status(con)
    except Exception:
        st.info("Прогнозы ещё не сохранены. Выполните ежедневное обновление.")
        return
    cols = st.columns(6)
    cols[0].metric("Сохранено", status["total"])
    cols[1].metric("Созрело", status["matured"])
    cols[2].metric("Ожидает", status["pending"])
    outcomes = _q(
        "SELECT avg(CASE WHEN direction_correct THEN 1.0 WHEN direction_correct=false THEN 0 END) hit," 
        "avg(abs(actual_return)) mae FROM forecast_outcomes WHERE outcome_status='matured'"
    )
    hit = outcomes.iloc[0].hit if not outcomes.empty else None
    mae = outcomes.iloc[0].mae if not outcomes.empty else None
    cols[3].metric("Directional", "—" if pd.isna(hit) else f"{hit:.1%}")
    cols[4].metric("Средняя ошибка", "—" if pd.isna(mae) else f"{mae:.2%}")
    cols[5].metric("Live status", status["live_status"])
    if status["matured"] < 20:
        st.warning("Выборка пока мала. Live-история накапливается.")
    render_forecast_vs_fact()


def render_forecast_vs_fact():
    forecasts = _q("SELECT DISTINCT secid FROM forecast_registry ORDER BY secid")
    if forecasts.empty:
        st.info("Нет сохранённых прогнозов для графика.")
        return
    secid = st.selectbox("Акция", forecasts.secid.tolist(), key="forecast_secid")
    horizons = _q(
        "SELECT DISTINCT horizon_sessions FROM forecast_registry WHERE secid=? ORDER BY 1", [secid]
    )
    horizon = st.selectbox("Горизонт", horizons.horizon_sessions.tolist(), key="forecast_horizon")
    versions = _q(
        "SELECT DISTINCT model_version FROM forecast_registry WHERE secid=? "
        "AND horizon_sessions=? ORDER BY 1",
        [secid, horizon],
    )
    version = st.selectbox("Версия модели", versions.model_version.tolist(), key="forecast_version")
    points = _q(
        "SELECT r.forecast_id,r.cutoff,r.current_price,r.qualitative_direction,r.confidence,r.model_version,"
        "o.actual_return,o.direction_correct,o.maturity_trade_date FROM forecast_registry r "
        "LEFT JOIN forecast_outcomes o USING(forecast_id) WHERE r.secid=? AND r.horizon_sessions=? "
        "AND r.model_version=? ORDER BY r.cutoff", [secid, horizon, version]
    )
    prices = _q(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>="
        "(SELECT min(cutoff) FROM forecast_registry WHERE secid=?) ORDER BY trade_date", [secid, secid]
    )
    figure = go.Figure(go.Scatter(x=prices.trade_date, y=prices.close, name="Фактическая цена"))
    colors = {"small_positive": "green", "small_negative": "red", "neutral": "gold",
              "unknown": "gray"}
    for direction, group in points.groupby("qualitative_direction"):
        figure.add_trace(go.Scatter(
            x=group.cutoff, y=group.current_price, mode="markers", name=direction,
            marker={"size": 11, "color": colors.get(direction, "gray")},
            customdata=group[["confidence", "actual_return", "direction_correct", "model_version"]],
            hovertemplate="Дата %{x}<br>Цена %{y}<br>Confidence %{customdata[0]}<br>"
            "Доходность %{customdata[1]}<br>Результат %{customdata[2]}<br>"
            "Версия %{customdata[3]}<extra></extra>",
        ))
    st.plotly_chart(figure, use_container_width=True)
    st.caption("Метки показывают реально сохранённые прогнозы; ретроспективные прогнозы не создаются.")
    if not points.empty:
        selected = st.selectbox("Прогноз для диапазона", points.forecast_id.tolist())
        render_range(selected)


def render_range(forecast_id):
    row = _q(
        "SELECT cutoff,secid,horizon_sessions,current_price,median_price,range_50_low,range_50_high,"
        "range_80_low,range_80_high,range_90_low,range_90_high FROM forecast_registry WHERE forecast_id=?",
        [forecast_id],
    )
    if row.empty:
        return
    item = row.iloc[0]
    path = _q(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>=? "
        "ORDER BY trade_date LIMIT ?", [item.secid, item.cutoff, int(item.horizon_sessions) + 1]
    )
    figure = go.Figure(go.Scatter(x=path.trade_date, y=path.close, name="Фактическая траектория"))
    end_date = path.trade_date.iloc[-1] if not path.empty else item.cutoff
    for label, low, high, color in (
        ("50%", item.range_50_low, item.range_50_high, "green"),
        ("80%", item.range_80_low, item.range_80_high, "orange"),
        ("90%", item.range_90_low, item.range_90_high, "gray"),
    ):
        if pd.notna(low) and pd.notna(high):
            figure.add_trace(go.Scatter(x=[end_date, end_date], y=[low, high], mode="lines+markers",
                                        name=f"Конечный диапазон {label}", line={"color": color}))
    st.plotly_chart(figure, use_container_width=True)
    st.caption("Дневная прогнозная траектория не дорисовывается: показан только конечный interval marker.")


def render_track_record():
    st.header("Model Track Record")
    st.warning("Live, historical, pseudo-OOS и backtest не смешиваются. Главный слой — live.")
    st.subheader("Live")
    st.dataframe(_q("SELECT * FROM model_version_scorecards ORDER BY active_from"), use_container_width=True)
    st.subheader("Live scorecards")
    st.dataframe(_q("SELECT * FROM forecast_scorecards ORDER BY model_version,horizon_sessions"),
                 use_container_width=True)
    st.subheader("Learning journal")
    st.dataframe(_q("SELECT * FROM forecast_learning_journal ORDER BY created_at DESC"),
                 use_container_width=True)


def render_quality():
    st.header("Качество прогнозов")
    try:
        with connection() as con:
            status = forecast_status(con)
    except Exception:
        st.info("Live-история пока не создана.")
        return
    columns = st.columns(6)
    columns[0].metric("Прогнозов", status["total"])
    columns[1].metric("Созрело", status["matured"])
    columns[2].metric("Ожидает", status["pending"])
    columns[3].metric("Статус", status["live_status"])
    columns[4].metric("Pending outcome records", status["pending_outcome_records"])
    columns[5].metric("Фактически оценено", status["evaluated"])
    if status["matured"] < 20:
        st.warning(
            "Live-история пока накапливается; pending outcome records не являются "
            "созревшими результатами, статистические выводы преждевременны."
        )
    st.dataframe(_q("SELECT * FROM forecast_scorecards ORDER BY horizon_sessions"),
                 use_container_width=True, hide_index=True)
    st.subheader("Последние ошибки")
    st.dataframe(_q("SELECT secid,horizon_sessions,error_category,causality_warning,created_at "
                    "FROM forecast_learning_journal WHERE error_category<>'no_direction_error' "
                    "ORDER BY created_at DESC LIMIT 20"), use_container_width=True, hide_index=True)


def render_update_history():
    st.header("История обновлений")
    frame = _q("SELECT started_at Дата,update_type Тип,duration_seconds Время,"
               "http_requests Requests,rows_inserted Rows,errors Errors,new_forecasts \"New forecasts\","
               "matured_forecasts \"Matured forecasts\",status Статус FROM daily_update_runs "
               "ORDER BY started_at DESC")
    if frame.empty:
        st.info("История обновлений пока пуста.")
    else:
        st.dataframe(frame, use_container_width=True, hide_index=True)
