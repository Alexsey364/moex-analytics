"""BASIC Stage 30 data-development dashboard."""

import pandas as pd
import streamlit as st

from moex_analytics.dashboard.data_access import read_connection, table_exists


def _scalar(con, sql: str, default=0):
    try:
        row = con.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def render() -> None:
    st.header("Развитие базы")
    st.caption("Расширение исследовательских данных. Production-модели не изменяются.")
    with read_connection() as con:
        securities = _scalar(con, "SELECT count(DISTINCT secid) FROM moex_equity_eod")
        active = _scalar(
            con,
            """SELECT count(*) FROM market_history_universe WHERE is_active
            AND secid IN (SELECT DISTINCT secid FROM moex_equity_eod)""",
        )
        inactive = max(int(securities) - int(active), 0)
        eod = _scalar(con, "SELECT count(*) FROM moex_equity_eod")
        fundamentals = _scalar(
            con,
            "SELECT count(*) FROM issuer_fundamental_values WHERE validation_status='validated'",
        )
        periods = _scalar(
            con,
            """SELECT count(DISTINCT (issuer,period_end)) FROM issuer_fundamental_values
            WHERE validation_status='validated'""",
        )
        dividends = _scalar(con, "SELECT count(*) FROM dividends")
        futures = _scalar(con, "SELECT count(*) FROM sber_futures_daily")
        context = _scalar(con, "SELECT count(*) FROM stage30_context_features")
        cols = st.columns(4)
        cols[0].metric("Исторические бумаги", f"549 → {securities}")
        cols[1].metric("Активные / неактивные", f"{active} / {inactive}")
        cols[2].metric("EOD наблюдения", f"403 923 → {eod:,}".replace(",", " "))
        cols[3].metric("Прогресс к 1000", f"{min(securities / 1000, 1):.0%}")
        st.progress(min(float(securities) / 1000, 1.0))
        st.write({"validated fundamentals": fundamentals, "fundamental periods": periods,
                  "dividends": dividends, "futures": futures, "context features": context})
        if table_exists(con, "stage30_context_coverage"):
            frame = con.execute(
                """SELECT dataset_family,series_id,rows,quality_status,limitation
                FROM stage30_context_coverage ORDER BY dataset_family,series_id"""
            ).df()
            frame["status"] = frame.quality_status.map({
                "usable": "🟢 собрано хорошо", "partial": "🟡 собирается",
                "missing": "🔴 важный пробел", "requires_paid_data": "🔵 платные данные",
            }).fillna("🟡 собирается")
            frame["expected_value"] = frame.dataset_family.map({
                "rates": "high", "fx": "high", "sector": "medium",
                "commodities": "unknown",
            }).fillna("unknown")
            st.subheader("Что ещё нужно собрать")
            st.dataframe(frame[["dataset_family", "series_id", "rows", "status",
                                "expected_value", "limitation"]], use_container_width=True)
        if table_exists(con, "stage30_data_value_ledger"):
            ledger = con.execute(
                """SELECT dataset_family,oos_effect,horizons_helped,status,evidence
                FROM stage30_data_value_ledger WHERE run_id=(SELECT run_id FROM stage30_ablation_runs
                ORDER BY created_at DESC LIMIT 1) ORDER BY dataset_family"""
            ).df()
            st.subheader("Измеренная OOS-польза")
            st.dataframe(ledger if not ledger.empty else pd.DataFrame(), use_container_width=True)
    st.warning("Наличие данных не означает прогнозного преимущества. Promotion в production запрещён.")
