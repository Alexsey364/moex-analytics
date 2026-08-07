"""Dashboard pages for research-only user portfolio analytics."""

import streamlit as st

from moex_analytics.database import connection


def _q(sql):
    with connection(read_only=True) as con:
        return con.execute(sql).df()


def render_portfolio():
    st.header("Мой портфель")
    st.warning("Research only. Нет BUY/SELL рекомендаций.")
    st.dataframe(
        _q("SELECT * FROM portfolio_positions ORDER BY snapshot_id DESC,weight DESC"),
        use_container_width=True,
    )


def render_instruments():
    st.header("Карточки инструментов")
    st.dataframe(_q("SELECT * FROM portfolio_instruments ORDER BY secid"), use_container_width=True)


def render_comparison():
    st.header("Сравнение акций")
    st.dataframe(
        _q(
            "SELECT canonical_secid,count(*) observations,min(trade_date) date_from,max(trade_date) date_to FROM canonical_daily_prices WHERE canonical_secid IN (SELECT secid FROM portfolio_instruments) GROUP BY 1"
        ),
        use_container_width=True,
    )


def render_preferred():
    st.header("Обычка против префа")
    st.dataframe(_q("SELECT * FROM preferred_share_rules"), use_container_width=True)


def render_alpha():
    st.header("Alpha Research портфеля")
    st.dataframe(
        _q("SELECT * FROM instrument_alpha_results ORDER BY stability_score DESC"), use_container_width=True
    )


def render_factors():
    st.header("Факторная карта")
    st.dataframe(
        _q("SELECT * FROM portfolio_cross_instrument_factors ORDER BY status,abs(mean_ic) DESC"),
        use_container_width=True,
    )


def render_dividends():
    st.header("Дивидендный календарь")
    st.dataframe(
        _q("SELECT * FROM portfolio_dividend_calendar ORDER BY record_date"), use_container_width=True
    )


def render_risk():
    st.header("Риск портфеля")
    st.dataframe(_q("SELECT * FROM portfolio_risk_metrics"), use_container_width=True)


def render_correlations():
    st.header("Корреляции")
    st.caption("Корреляционная матрица строится только на общей PIT-выборке в risk pipeline.")
    st.dataframe(_q("SELECT * FROM portfolio_rebalancing_experiments"), use_container_width=True)


def render_scenarios():
    st.header("Сценарии — не прогноз")
    st.dataframe(_q("SELECT * FROM portfolio_scenarios"), use_container_width=True)


def render_allocations():
    st.header("Варианты распределения")
    st.warning("Оптимизация не гарантирует результат.")
    st.dataframe(_q("SELECT * FROM portfolio_rebalancing_experiments"), use_container_width=True)


def render_history():
    st.header("История портфеля")
    st.dataframe(_q("SELECT * FROM portfolio_snapshots ORDER BY created_at DESC"), use_container_width=True)


def render_quality():
    st.header("Качество данных")
    st.dataframe(
        _q("SELECT * FROM instrument_source_availability ORDER BY secid,source_type"),
        use_container_width=True,
    )


def render_open_source():
    st.header("Open-source аудит")
    st.dataframe(
        _q(
            "SELECT name,repository_url,license,activity_status,legal_reuse_status,integration_recommendation,audit_notes FROM external_project_audit"
        ),
        use_container_width=True,
    )


def render_real_portfolio():
    st.header("Мой реальный портфель")
    st.warning("Локальные позиции. Research only; BUY/SELL отсутствуют.")
    st.dataframe(_q("SELECT * FROM portfolio_snapshots ORDER BY created_at DESC"), use_container_width=True)
    st.dataframe(
        _q("SELECT * FROM portfolio_positions ORDER BY snapshot_id,weight DESC"), use_container_width=True
    )


def render_validation():
    st.header("Проверка alpha-кандидатов")
    st.caption("Purged expanding walk-forward, embargo, block bootstrap, HAC and sanity checks.")
    st.dataframe(
        _q("SELECT * FROM portfolio_alpha_validations ORDER BY status,secid"), use_container_width=True
    )


def render_regime_factors():
    st.header("Факторы по режимам")
    st.dataframe(
        _q("SELECT secid,feature,horizon,status,regime_json FROM portfolio_alpha_validations"),
        use_container_width=True,
    )


def render_okama():
    st.header("Сравнение с okama")
    st.dataframe(
        _q("SELECT * FROM portfolio_metric_reconciliation ORDER BY metric"), use_container_width=True
    )


def render_methodology_v14():
    st.header("Методология расчётов")
    st.markdown(
        "Каждая строка содержит формулы, период, confidence и experimental status. Исторически лучший портфель не является прогнозом."
    )
    st.dataframe(_q("SELECT * FROM external_method_audits"), use_container_width=True)


def render_risk_contribution():
    st.header("Вклад в риск")
    st.dataframe(
        _q("SELECT * FROM portfolio_factor_exposures WHERE factor LIKE 'risk_contribution:%'"),
        use_container_width=True,
    )


def render_dividend_flow():
    st.header("Дивидендный поток")
    st.warning("Estimated/scenario DPS не является объявленным дивидендом.")
    st.dataframe(
        _q("SELECT * FROM portfolio_dividend_outlook ORDER BY month,secid,scenario"), use_container_width=True
    )


def render_fundamental_readiness():
    st.header("Фундаментальная готовность")
    st.dataframe(_q("SELECT * FROM issuer_source_maps ORDER BY secid,metric"), use_container_width=True)


def render_backtest_portfolio():
    st.header("Backtest портфеля")
    st.caption("Native delayed-fill simulator; no live broker connection.")
    st.dataframe(_q("SELECT * FROM backtest_reconciliation"), use_container_width=True)


def render_external_methods():
    st.header("Внешние методы")
    st.dataframe(_q("SELECT * FROM external_method_audits"), use_container_width=True)
