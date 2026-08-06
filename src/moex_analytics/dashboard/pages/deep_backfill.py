"""Dashboard pages for deep historical backfill and common sample."""

import streamlit as st

from moex_analytics.database import connection


def _show(title, query):
    st.header(title)
    with connection() as con:
        try:
            st.dataframe(con.execute(query).df(), use_container_width=True)
        except Exception as exc:
            st.info(f"Данные ещё не построены: {exc}")


def render_zcyc():
    _show(
        "Глубина ZCYC",
        "select observation_date,count(*) tenors,min(zero_coupon_yield),max(zero_coupon_yield),min(quality_status) quality from deep_zcyc_archive group by observation_date order by observation_date desc",
    )


def render_futures():
    _show("Архив фьючерсов SBER", "select * from expired_sber_futures order by expiration desc")


def render_rolls():
    _show("История rollover", "select * from deep_futures_rolls order by roll_date desc,rule")


def render_universe():
    _show(
        "Динамическая вселенная",
        "select trade_date,count(*) size,min(trailing_turnover) threshold from dynamic_liquid_universe where eligible group by trade_date order by trade_date desc",
    )


def render_survivorship():
    _show("Искажение survivorship", "select * from survivorship_impact_daily order by abs(difference) desc")


def render_finance():
    _show(
        "Исторический финансовый сектор", "select * from historical_financial_sector order by trade_date desc"
    )


def render_intraday():
    _show("Внутридневное покрытие", "select * from deep_intraday_coverage order by secid,interval_minutes")


def render_ifrs():
    _show("МСФО review", "select * from ifrs_review_validation order by validation_status,document_id,metric")


def render_options():
    _show(
        "Исторические опционы",
        "select * from options_history_coverage order by history_accessible desc,expiration desc",
    )


def render_sample():
    _show(
        "Common sample",
        "select horizon,count(*) rows,min(trade_date),max(trade_date),sum(zcyc_available::int) zcyc,sum(futures_available::int) futures,sum(intraday_available::int) intraday from sber_predictive_common_sample group by horizon order by horizon",
    )


def render_tiers():
    _show("Coverage tiers", "select * from sber_coverage_tiers order by horizon,tier")


def render_readiness():
    _show("Готовность к модели", "select * from sber_model_readiness order by horizon")
