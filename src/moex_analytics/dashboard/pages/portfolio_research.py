"""Dashboard pages for research-only user portfolio analytics."""
import streamlit as st

from moex_analytics.database import connection


def _q(sql):
    with connection(read_only=True) as con: return con.execute(sql).df()
def render_portfolio():
    st.header("Мой портфель"); st.warning("Research only. Нет BUY/SELL рекомендаций."); st.dataframe(_q("SELECT * FROM portfolio_positions ORDER BY snapshot_id DESC,weight DESC"),use_container_width=True)
def render_instruments(): st.header("Карточки инструментов"); st.dataframe(_q("SELECT * FROM portfolio_instruments ORDER BY secid"),use_container_width=True)
def render_comparison(): st.header("Сравнение акций"); st.dataframe(_q("SELECT canonical_secid,count(*) observations,min(trade_date) date_from,max(trade_date) date_to FROM canonical_daily_prices WHERE canonical_secid IN (SELECT secid FROM portfolio_instruments) GROUP BY 1"),use_container_width=True)
def render_preferred(): st.header("Обычка против префа"); st.dataframe(_q("SELECT * FROM preferred_share_rules"),use_container_width=True)
def render_alpha(): st.header("Alpha Research портфеля"); st.dataframe(_q("SELECT * FROM instrument_alpha_results ORDER BY stability_score DESC"),use_container_width=True)
def render_factors(): st.header("Факторная карта"); st.dataframe(_q("SELECT * FROM portfolio_cross_instrument_factors ORDER BY status,abs(mean_ic) DESC"),use_container_width=True)
def render_dividends(): st.header("Дивидендный календарь"); st.dataframe(_q("SELECT * FROM portfolio_dividend_calendar ORDER BY record_date"),use_container_width=True)
def render_risk(): st.header("Риск портфеля"); st.dataframe(_q("SELECT * FROM portfolio_risk_metrics"),use_container_width=True)
def render_correlations(): st.header("Корреляции"); st.caption("Корреляционная матрица строится только на общей PIT-выборке в risk pipeline."); st.dataframe(_q("SELECT * FROM portfolio_rebalancing_experiments"),use_container_width=True)
def render_scenarios(): st.header("Сценарии — не прогноз"); st.dataframe(_q("SELECT * FROM portfolio_scenarios"),use_container_width=True)
def render_allocations(): st.header("Варианты распределения"); st.warning("Оптимизация не гарантирует результат."); st.dataframe(_q("SELECT * FROM portfolio_rebalancing_experiments"),use_container_width=True)
def render_history(): st.header("История портфеля"); st.dataframe(_q("SELECT * FROM portfolio_snapshots ORDER BY created_at DESC"),use_container_width=True)
def render_quality(): st.header("Качество данных"); st.dataframe(_q("SELECT * FROM instrument_source_availability ORDER BY secid,source_type"),use_container_width=True)
def render_open_source(): st.header("Open-source аудит"); st.dataframe(_q("SELECT name,repository_url,license,activity_status,legal_reuse_status,integration_recommendation,audit_notes FROM external_project_audit"),use_container_width=True)