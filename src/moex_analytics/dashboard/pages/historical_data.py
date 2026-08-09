"""Historical coverage pages for basic and advanced dashboard modes."""

import pandas as pd
import streamlit as st

from moex_analytics.database import connection
from moex_analytics.historical_data.core import ensure_schema


def _coverage() -> pd.DataFrame:
    with connection(read_only=False) as con:
        ensure_schema(con)
        return con.execute("SELECT * FROM historical_data_coverage ORDER BY instrument,dataset_family").df()


def render_advanced() -> None:
    st.header("Покрытие исторических данных")
    frame = _coverage()
    if frame.empty:
        st.info("Аудит ещё не выполнен. Запустите complete-historical-data-audit.")
        return
    colors = {"complete": "🟢", "partial": "🟡", "missing": "🔴"}
    display_status = frame.current_status.map(colors).fillna("🔴")
    display_status = display_status.mask(frame.access_class == "paid/restricted", "🔵")
    frame.insert(0, "coverage", display_status)
    selected = st.selectbox("Инструмент", ["Все", *sorted(frame.instrument.unique())])
    if selected != "Все":
        frame = frame[frame.instrument == selected]
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption("Синий статус означает платный или ограниченный источник; красный — данных нет.")


def render_basic() -> None:
    st.header("Качество данных")
    frame = _coverage()
    if frame.empty:
        st.warning("Покрытие ещё не рассчитано. Решения используют только ранее проверенные данные.")
        return
    with connection(read_only=False) as con:
        table_rows = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        tables = {row[0] for row in table_rows}
        if "issuer_fundamental_values" in tables:
            fundamentals = con.execute(
                """SELECT issuer,count(*) observations,count(DISTINCT period_end) periods,
                min(period_end) earliest,max(period_end) latest
                FROM issuer_fundamental_values WHERE validation_status='validated'
                GROUP BY issuer ORDER BY issuer"""
            ).df()
            st.subheader("Fundamental history: до этапа → после")
            baseline = {"SBER": 53}
            fundamentals.insert(1, "before", fundamentals.issuer.map(baseline).fillna(0).astype(int))
            st.dataframe(fundamentals, hide_index=True, use_container_width=True)
        if "tradable_on_date_universe" in tables:
            securities, rows = con.execute(
                "SELECT count(DISTINCT secid),count(*) FROM tradable_on_date_universe"
            ).fetchone()
            st.subheader("Historical universe")
            st.write(
                f"PIT membership до этапа: 0 → tradable-on-date: {securities} securities / {rows} EOD rows"
            )
        if "market_history_jobs" in tables:
            total, complete, errors = con.execute(
                """SELECT count(*),count(*) filter(where status='completed'),
                count(*) filter(where status='failed') FROM market_history_jobs"""
            ).fetchone()
            securities, rows = con.execute(
                "SELECT count(distinct secid),count(*) FROM moex_equity_eod"
            ).fetchone()
            active, inactive = con.execute(
                """SELECT count(distinct e.secid) filter(where u.is_traded),
                count(distinct e.secid) filter(where NOT u.is_traded)
                FROM moex_equity_eod e JOIN historical_equity_universe u USING(secid)"""
            ).fetchone()
            last = (
                con.execute(
                    "SELECT finished_at,cursor_hash FROM market_history_batch_runs "
                    "ORDER BY finished_at DESC LIMIT 1"
                ).fetchone()
                if "market_history_batch_runs" in tables
                else None
            )
            st.subheader("Historical equity universe backfill")
            st.progress(min(securities / 2623, 1.0), text=f"Бумаги: 156 / 2623 → {securities} / 2623")
            st.progress(
                min(complete / max(total, 1), 1.0), text=f"Задания: 241 / 3428 → {complete} / {total}"
            )
            st.write(f"EOD rows: 123637 → {rows}; active: {active}; inactive: {inactive}; errors: {errors}")
            st.caption(f"Последний checkpoint: {last or 'batch history ещё не создана'}")
        fx = con.execute(
            """SELECT count(*) FROM macro_observations
            WHERE series_id IN ('cbr_usd_rub','cbr_eur_rub','cbr_cny_rub')"""
        ).fetchone()[0]
        st.subheader("FX")
        st.write(f"Coverage catalog → validated official CBR history: {fx} observations")
    good = frame[frame.current_status == "complete"]
    partial = frame[frame.current_status == "partial"]
    missing = frame[frame.current_status == "missing"]
    st.success(f"Что собрано хорошо: {len(good)} наборов")
    st.warning(f"Что собрано частично: {len(partial)} наборов")
    st.error(f"Чего не хватает: {len(missing)} наборов")
    critical = missing[missing.analytical_priority.isin(["critical", "high"])]
    if critical.empty:
        st.info("Критических незакрытых пробелов, влияющих на текущее решение, не выявлено.")
    else:
        st.info(f"На надёжность решения сейчас могут влиять {len(critical)} приоритетных пробелов.")
        columns = ["instrument", "dataset_family", "blocker", "recommended_action"]
        st.dataframe(critical[columns], hide_index=True, use_container_width=True)
