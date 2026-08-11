"""Forecast versus fact and live track-record dashboard pages."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from moex_analytics.dashboard.investor_visuals import live_progress
from moex_analytics.dashboard.visual_semantics import color_for
from moex_analytics.database import connection
from moex_analytics.portfolio_research.forecast_scorecards import forecast_status
from moex_analytics.portfolio_research.live_evidence import live_evidence_status


def _q(sql, params=None):
    try:
        with connection(read_only=True) as con:
            return con.execute(sql, params or []).df()
    except Exception:
        return pd.DataFrame()


def render_basic():
    st.header("Как программа оценивает будущее")
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
    cols[3].metric("Верное направление", "—" if pd.isna(hit) else f"{hit:.1%}")
    cols[4].metric("Среднее отклонение", "—" if pd.isna(mae) else f"{mae:.2%}")
    cols[5].metric(
        "Статус проверки",
        "⚪ Наблюдений мало" if status["matured"] < 50 else "🟡 Идёт проверка",
    )
    if status["matured"] < 50:
        st.warning("Реальных наблюдений пока мало. Сильные выводы и числовые вероятности запрещены.")
    render_forecast_vs_fact()


def render_forecast_vs_fact():
    forecasts = _q("SELECT DISTINCT secid FROM forecast_registry ORDER BY secid")
    if forecasts.empty:
        st.info("Нет сохранённых прогнозов для графика.")
        return
    secid = st.selectbox("Акция", forecasts.secid.tolist(), key="forecast_secid")
    horizons = _q("SELECT DISTINCT horizon_sessions FROM forecast_registry WHERE secid=? ORDER BY 1", [secid])
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
        "AND r.model_version=? ORDER BY r.cutoff",
        [secid, horizon, version],
    )
    prices = _q(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? AND trade_date>="
        "(SELECT min(cutoff) FROM forecast_registry WHERE secid=?) ORDER BY trade_date",
        [secid, secid],
    )
    figure = go.Figure(go.Scatter(x=prices.trade_date, y=prices.close, name="Фактическая цена"))
    colors = {
        "small_positive": color_for("positive"),
        "small_negative": color_for("negative"),
        "neutral": color_for("mixed"),
        "unknown": color_for("insufficient"),
    }
    for direction, group in points.groupby("qualitative_direction"):
        figure.add_trace(
            go.Scatter(
                x=group.cutoff,
                y=group.current_price,
                mode="markers",
                name=direction,
                marker={"size": 11, "color": colors.get(direction, "gray")},
                customdata=group[["confidence", "actual_return", "direction_correct", "model_version"]],
                hovertemplate="Дата %{x}<br>Цена %{y}<br>Confidence %{customdata[0]}<br>"
                "Доходность %{customdata[1]}<br>Результат %{customdata[2]}<br>"
                "Версия %{customdata[3]}<extra></extra>",
            )
        )
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
        "ORDER BY trade_date LIMIT ?",
        [item.secid, item.cutoff, int(item.horizon_sessions) + 1],
    )
    figure = go.Figure(go.Scatter(x=path.trade_date, y=path.close, name="Фактическая траектория"))
    end_date = path.trade_date.iloc[-1] if not path.empty else item.cutoff
    for label, low, high, color in (
        ("50%", item.range_50_low, item.range_50_high, color_for("positive")),
        ("80%", item.range_80_low, item.range_80_high, color_for("caution")),
        ("90%", item.range_90_low, item.range_90_high, color_for("insufficient")),
    ):
        if pd.notna(low) and pd.notna(high):
            figure.add_trace(
                go.Scatter(
                    x=[end_date, end_date],
                    y=[low, high],
                    mode="lines+markers",
                    name=f"Конечный диапазон {label}",
                    line={"color": color},
                )
            )
    st.plotly_chart(figure, use_container_width=True)
    st.caption("Дневная прогнозная траектория не дорисовывается: показан только конечный interval marker.")


def render_track_record():
    st.header("Model Track Record")
    st.warning("Live, historical, pseudo-OOS и backtest не смешиваются. Главный слой — live.")
    st.subheader("Live")
    st.dataframe(_q("SELECT * FROM model_version_scorecards ORDER BY active_from"), use_container_width=True)
    st.subheader("Live scorecards")
    st.dataframe(
        _q("SELECT * FROM forecast_scorecards ORDER BY model_version,horizon_sessions"),
        use_container_width=True,
    )
    st.subheader("Learning journal")
    st.dataframe(
        _q("SELECT * FROM forecast_learning_journal ORDER BY created_at DESC"), use_container_width=True
    )


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
    st.dataframe(
        _q("SELECT * FROM forecast_scorecards ORDER BY horizon_sessions"),
        use_container_width=True,
        hide_index=True,
    )
    st.subheader("Последние ошибки")
    st.dataframe(
        _q(
            "SELECT secid,horizon_sessions,error_category,causality_warning,created_at "
            "FROM forecast_learning_journal WHERE error_category<>'no_direction_error' "
            "ORDER BY created_at DESC LIMIT 20"
        ),
        use_container_width=True,
        hide_index=True,
    )


def render_update_history():
    st.header("История обновлений")
    frame = _q(
        "SELECT started_at Дата,update_type Тип,duration_seconds Время,"
        'http_requests Requests,rows_inserted Rows,errors Errors,new_forecasts "New forecasts",'
        'matured_forecasts "Matured forecasts",status Статус FROM daily_update_runs '
        "ORDER BY started_at DESC"
    )
    if frame.empty:
        st.info("История обновлений пока пуста.")
    else:
        st.dataframe(frame, use_container_width=True, hide_index=True)


def render_maturity_calendar():
    st.header("Когда начнётся реальная проверка")
    st.caption(
        "Созревание считается только по фактически наблюдаемым биржевым сессиям. "
        "Будущая дата — ориентир по будним дням до появления официальной торговой сессии."
    )
    frame = _q(
        "SELECT horizon_sessions Горизонт,count(*) Прогнозов,min(next_expected_maturity) "
        '"Ожидаемая дата",min(sessions_remaining) "Осталось сессий",'
        "sum(CASE WHEN maturity_status='matured_confirmed' THEN 1 ELSE 0 END) Созрело,"
        'min(date_basis) "Основание даты" FROM forecast_maturity_calendar GROUP BY 1 ORDER BY 1'
    )
    if frame.empty:
        st.info("Календарь ещё не рассчитан. Выполните быстрое ежедневное обновление.")
        return
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.warning("Расчётная дата не создаёт outcome. Результат появляется только после реальных торгов.")


def render_live_evidence():
    st.header("Что программа уже доказала")
    try:
        with connection() as con:
            status = live_evidence_status(con)
    except Exception:
        st.info("Live evidence ещё не рассчитан.")
        return
    cols = st.columns(4)
    cols[0].metric("Историческая проверка", "research / pseudo-OOS")
    cols[1].metric("Live matured", status["matured"])
    cols[2].metric("Подтверждённых live-моделей", status["confirmed_live_models"])
    cols[3].metric("Ожидают", status["pending"])
    if status["matured"] == 0:
        st.info("Реальная проверка ещё не началась.")
    elif status["research_review_recommended"]:
        st.warning("Накоплен триггер для исследовательского review. Auto-promotion запрещён.")
    st.caption(
        "Пороговые названия показывают объём накопления, а не гарантируют статистическую достаточность. "
        "Shadow-результаты не влияют на пользовательское инвестиционное решение."
    )
    st.dataframe(
        _q(
            "SELECT secid,horizon_sessions,model_version,historical_oos_n,live_n,"
            "live_direction_score,live_mae,live_calibration,drift_status,evidence_band "
            "FROM live_evidence_meter ORDER BY secid,horizon_sessions"
        ),
        use_container_width=True,
        hide_index=True,
    )
    latest = _q(
        "SELECT matured_new,matured_total,shadows_evaluated,finished_at FROM live_evidence_runs "
        "ORDER BY finished_at DESC LIMIT 1"
    )
    if not latest.empty and int(latest.iloc[0].matured_new):
        matured_new = int(latest.iloc[0].matured_new)
        st.success(f"Созрело {matured_new} новых прогнозов. Качество моделей пересчитано.")


def render_live_validation():
    st.header("Реальная проверка")
    totals = _q(
        "SELECT (SELECT count(*) FROM forecast_registry) total,"
        "(SELECT count(*) FROM forecast_outcomes WHERE outcome_status='matured') matured,"
        "(SELECT count(*) FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured' AND ((r.qualitative_direction IN ('↑','small_positive') "
        "AND o.actual_return>0) OR (r.qualitative_direction IN ('↓','small_negative') "
        "AND o.actual_return<0))) correct,"
        "(SELECT count(*) FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured' AND (((r.qualitative_direction IN ('↑','small_positive') "
        "AND o.actual_return<=0) OR (r.qualitative_direction IN ('↓','small_negative') "
        "AND o.actual_return>=0)) OR (r.qualitative_direction IN ('→','neutral') "
        "AND abs(o.actual_return)>0.01))) wrong,"
        "(SELECT count(*) FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured' AND r.qualitative_direction IN ('→','neutral') "
        "AND abs(o.actual_return)<=0.01) neutral"
    )
    if totals.empty:
        st.info("Live validation ещё не рассчитан.")
        return
    row = totals.iloc[0]
    columns = st.columns(6)
    columns[0].metric("Всего", int(row.total))
    columns[1].metric("Созрело", int(row.matured))
    columns[2].metric("В ожидании", int(row.total - row.matured))
    columns[3].metric("Верное направление", int(row.correct))
    columns[4].metric("Ошибки", int(row.wrong))
    columns[5].metric("Нейтральные", int(row.neutral))
    independent = _q(
        "SELECT count(*) n FROM (SELECT DISTINCT r.secid,r.cutoff,r.horizon_sessions "
        "FROM forecast_registry r JOIN forecast_outcomes o USING(forecast_id) "
        "WHERE o.outcome_status='matured')"
    )
    independent_n = int(independent.iloc[0].n) if not independent.empty else 0
    st.info(
        f"{int(row.matured)} записей прогнозов = {independent_n} независимых рыночных исходов. "
        "Несколько записей могут относиться к одной бумаге, дате и фактическому движению."
    )
    if int(row.matured) < 50:
        st.warning(
            "⚪ Выборка пока слишком мала для вывода о качестве модели. "
            "Это первый настоящий live-экзамен; числовая вероятность не публикуется."
        )
        st.caption(
            "Первый торговый день оказался слабым для сохранённых состояний модели, "
            "но одна сессия не позволяет оценить её качество."
        )
    progress = live_progress(int(row.correct), int(row.wrong), int(row.neutral), int(row.total - row.matured))
    if progress is None:
        st.info(
            "⚪ Реальная проверка ещё не началась. Accuracy не вычисляется до появления matured outcomes."
        )
    else:
        st.plotly_chart(progress, use_container_width=True, key="live_scorecard_progress")
    scorecards = _q(
        "SELECT dimension_value secid,horizon_sessions horizon,model_version,observations,"
        "unique_cutoffs,effective_n,direction_accuracy,balanced_accuracy,mae,rmse,"
        "median_return_error,mean_favorable_excursion,mean_adverse_excursion,coverage_90,"
        "neutral_hit_rate,sample_status FROM live_validation_scorecards ORDER BY secid,horizon"
    )
    st.subheader("По акциям и горизонтам")
    if scorecards.empty:
        st.info("Нет созревших результатов; scorecards готовы к первому outcome.")
    else:
        secid = st.selectbox("Акция", sorted(scorecards.secid.unique()), key="live_validation_stock")
        st.dataframe(scorecards[scorecards.secid == secid], use_container_width=True, hide_index=True)
    st.subheader("Baseline и shadow — только same-date")
    st.dataframe(
        _q(
            "SELECT secid,horizon_sessions,competitor,matched_dates,effective_n,advantage,status,reason "
            "FROM live_model_duels ORDER BY secid,horizon_sessions,competitor"
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Если competitor не был сохранён до результата на той же дате, "
        "duel не реконструируется задним числом."
    )
    examples = _q(
        "SELECT secid,horizon_sessions,prediction,actual_return,magnitude_error,direction_result,"
        "regime,model_disagreement,causality_warning FROM live_error_diagnostics "
        "ORDER BY created_at DESC LIMIT 20"
    )
    st.subheader("Реальные правильные и ошибочные примеры")
    if examples.empty:
        st.info("Реальных matured examples пока нет.")
    else:
        st.dataframe(examples, use_container_width=True, hide_index=True)
    render_forecast_vs_fact()
