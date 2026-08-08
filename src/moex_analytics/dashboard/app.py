"""Run with: python -m moex_analytics.dashboard.app."""

import subprocess
import sys

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

if get_script_run_ctx() is None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            __file__,
            "--server.address",
            "localhost",
            "--server.port",
            "8501",
        ],
        check=True,
    )
    raise SystemExit

from moex_analytics.dashboard.data_access import DatabaseUnavailable, database_summary
from moex_analytics.dashboard.launcher import mark_current_process
from moex_analytics.dashboard.navigation import group_advanced_pages, navigation_pages
from moex_analytics.dashboard.pages import (
    alpha_research,
    analytics,
    critical_data,
    data_quality,
    database_status,
    deep_backfill,
    forecast_scorecard,
    fundamentals,
    human_portfolio,
    instrument,
    macro,
    methodology,
    overview,
    portfolio_research,
    predictive_foundation,
    sber_decision,
    sber_intelligence,
    sber_operational,
    unblocked_experiment,
    update_data,
)
from moex_analytics.database import database_path, init_database

st.set_page_config(page_title="Аналитика рынка MOEX", layout="wide")
mark_current_process()
st.title("Аналитика рынка MOEX")

if not database_path().exists():
    st.warning("База данных ещё не создана.")
    if st.button("Создать пустую базу"):
        init_database()
        st.success("База создана. Откройте страницу «Обновление данных».")
        st.rerun()
    st.stop()

try:
    summary = database_summary()
except DatabaseUnavailable as exc:
    st.error(str(exc))
    st.info("Закройте другую операцию с базой и обновите страницу.")
    st.stop()

if not summary.get("ready"):
    st.warning("База создана, но схема неполная. Выполните начальную настройку.")

top = st.columns(4)
top[0].metric("Состояние базы", "Готова" if summary.get("ready") else "Настройка")
top[1].metric("Последнее обновление", str(summary.get("last_load") or "—"))
top[2].metric("Проблем качества", summary.get("issues", 0))
top[3].metric(
    "Диапазон",
    f"{summary.get('date_from', '—')} — {summary.get('date_to', '—')}",
)

advanced_pages = {
    "Model Track Record": forecast_scorecard.render_track_record,
    "Company Valuation": portfolio_research.render_company_valuation,
    "Regime Risk": portfolio_research.render_regime_risk_v15,
    "Portfolio Action Map": portfolio_research.render_action_map,
    "Portfolio Alternatives — real": portfolio_research.render_alternatives_v15,
    "Data Quality — intelligence": portfolio_research.render_data_quality_v15,
    "Мой реальный портфель": portfolio_research.render_real_portfolio,
    "Проверка alpha-кандидатов": portfolio_research.render_validation,
    "Факторы по режимам": portfolio_research.render_regime_factors,
    "Сравнение с okama": portfolio_research.render_okama,
    "Методология расчётов — портфель": portfolio_research.render_methodology_v14,
    "Вклад в риск": portfolio_research.render_risk_contribution,
    "Дивидендный поток": portfolio_research.render_dividend_flow,
    "Фундаментальная готовность": portfolio_research.render_fundamental_readiness,
    "Backtest портфеля": portfolio_research.render_backtest_portfolio,
    "Внешние методы": portfolio_research.render_external_methods,
    "Мой портфель": portfolio_research.render_portfolio,
    "Карточки инструментов": portfolio_research.render_instruments,
    "Сравнение акций": portfolio_research.render_comparison,
    "Обычка против префа": portfolio_research.render_preferred,
    "Alpha Research портфеля": portfolio_research.render_alpha,
    "Факторная карта": portfolio_research.render_factors,
    "Дивидендный календарь — портфель": portfolio_research.render_dividends,
    "Риск портфеля": portfolio_research.render_risk,
    "Корреляции портфеля": portfolio_research.render_correlations,
    "Сценарии портфеля": portfolio_research.render_scenarios,
    "Варианты распределения": portfolio_research.render_allocations,
    "История портфеля": portfolio_research.render_history,
    "Качество данных портфеля": portfolio_research.render_quality,
    "Open-source аудит": portfolio_research.render_open_source,
    "Feature Registry": alpha_research.render_registry,
    "Feature Importance": alpha_research.render_importance,
    "Feature Stability": alpha_research.render_stability,
    "Market State — research": alpha_research.render_market_state,
    "Regime Discovery": alpha_research.render_regimes,
    "Alpha Decay": alpha_research.render_decay,
    "Interaction Matrix": alpha_research.render_interactions,
    "Production Candidates — research": alpha_research.render_candidates,
    "Research Journal": alpha_research.render_journal,
    "Эксперимент направления SBER": unblocked_experiment.render_direction,
    "Доступные наборы данных": unblocked_experiment.render_datasets,
    "Модульные common samples": unblocked_experiment.render_samples,
    "Результаты по горизонтам — эксперимент": unblocked_experiment.render_horizons,
    "Калибровка вероятностей": unblocked_experiment.render_calibration,
    "Стабильность признаков": unblocked_experiment.render_stability,
    "Добавочная ценность данных — эксперимент": unblocked_experiment.render_value,
    "Текущий экспериментальный прогноз": unblocked_experiment.render_forecast,
    "Покупать сейчас или ждать — эксперимент": unblocked_experiment.render_timing,
    "Live shadow forecasts": unblocked_experiment.render_shadow,
    "Глубина ZCYC": deep_backfill.render_zcyc,
    "Архив фьючерсов SBER": deep_backfill.render_futures,
    "История rollover": deep_backfill.render_rolls,
    "Динамическая вселенная": deep_backfill.render_universe,
    "Искажение survivorship": deep_backfill.render_survivorship,
    "Исторический финансовый сектор — 11B": deep_backfill.render_finance,
    "Внутридневное покрытие": deep_backfill.render_intraday,
    "МСФО review": deep_backfill.render_ifrs,
    "Исторические опционы": deep_backfill.render_options,
    "Common sample": deep_backfill.render_sample,
    "Coverage tiers": deep_backfill.render_tiers,
    "Готовность к модели": deep_backfill.render_readiness,
    "Историческая вселенная": critical_data.render_universe,
    "Survivorship bias": critical_data.render_survivorship,
    "Исторический финансовый сектор": critical_data.render_finance,
    "Кривая ZCYC": critical_data.render_zcyc,
    "Непрерывные фьючерсы SBER": critical_data.render_futures,
    "Roll history": critical_data.render_rolls,
    "МСФО SBER — архив": critical_data.render_ifrs,
    "Опционы: доступность и качество": critical_data.render_options,
    "Внутридневные сессии": critical_data.render_intraday,
    "Готовность данных к прогнозу": critical_data.render_readiness,
    "Повторный ablation": critical_data.render_ablation,
    "Карта данных прогноза": predictive_foundation.render_catalog,
    "Состояние всего рынка": predictive_foundation.render_market,
    "Ширина рынка": predictive_foundation.render_breadth,
    "Финансовый сектор": predictive_foundation.render_finance,
    "Фьючерсы SBER": predictive_foundation.render_futures,
    "Опционы и ожидаемая волатильность": predictive_foundation.render_options,
    "Ставки и кривая ОФЗ": predictive_foundation.render_rates,
    "Ликвидность и перетоки": predictive_foundation.render_liquidity,
    "Межрыночные связи": predictive_foundation.render_cross_market,
    "Структурные режимы": predictive_foundation.render_regimes,
    "Полнота данных": predictive_foundation.render_coverage,
    "Добавочная ценность блоков": predictive_foundation.render_ablation,
    "Оперативный бизнес SBER": sber_operational.render_business,
    "Nowcast SBER": sber_operational.render_nowcast,
    "Ранние предупреждения": sber_operational.render_warnings,
    "Аудит зон покупки": sber_operational.render_zone_audit,
    "Почему такая доля": sber_operational.render_size,
    "Журнал решений": sber_operational.render_journal,
    "Реальные результаты": sber_operational.render_outcomes,
    "Версии модели": sber_operational.render_versions,
    "Информационная лента SBER": sber_intelligence.render_feed,
    "Календарь SBER": sber_intelligence.render_calendar,
    "Реакция на события": sber_intelligence.render_reactions,
    "Ожидания рынка": sber_intelligence.render_expectations,
    "Что изменилось": sber_intelligence.render_changes,
    "Оперативная статистика SBER": sber_intelligence.render_operational,
    "Информационное качество": sber_intelligence.render_quality,
    "Решение по SBER": sber_decision.render_decision,
    "МСФО и РСБУ SBER": sber_decision.render_reporting,
    "Дивиденд SBER": sber_decision.render_dividend,
    "Оценка SBER": sber_decision.render_valuation,
    "Зоны покупки": sber_decision.render_zones,
    "Триггеры решения": sber_decision.render_triggers,
    "Историческая проверка решения": sber_decision.render_backtest,
    "История отчётности SBER": fundamentals.render_reporting_history,
    "История фундаментала SBER": fundamentals.render_fundamental_history,
    "Текущая оценка SBER": fundamentals.render_current_valuation,
    "История оценок SBER": fundamentals.render_valuation_history,
    "История ошибок фундаментала": fundamentals.render_error_history,
    "Качество фундаментальных данных": fundamentals.render_fundamental_quality,
    "Фундаментал SBER": fundamentals.render_fundamental,
    "Мультипликаторы SBER": fundamentals.render_multiples,
    "Сценарная оценка SBER": fundamentals.render_scenarios,
    "Историческая проверка фундаментала": fundamentals.render_validation,
    "Источники и качество SBER": fundamentals.render_sources,
    "Аудит макромодели": macro.render_audit,
    "Макроэкономика": macro.render_macro,
    "Макрофакторы инструмента": macro.render_instrument_factors,
    "Прогнозные диапазоны": macro.render_forecasts,
    "Сравнение моделей": macro.render_comparison,
    "Календарь событий": macro.render_events,
    "Аналитика инструмента": analytics.render_summary,
    "Факторы": analytics.render_factors,
    "Исторические аналоги": analytics.render_analogues,
    "Режим рынка": analytics.render_regimes,
    "Проверка модели": analytics.render_validation,
    "Обзор": overview.render,
    "Инструмент": instrument.render,
    "Качество данных": data_quality.render,
    "Состояние базы": database_status.render,
    "Обновление данных": update_data.render,
    "Методология": methodology.render,
}
basic_pages = {
    "Сегодня": human_portfolio.render_today,
    "Мой портфель": human_portfolio.render_portfolio,
    "Куда вложить пополнение": human_portfolio.render_allocation,
    "Акции": human_portfolio.render_stocks,
    "Спросить про портфель": human_portfolio.render_ask,
    "Как программа прогнозирует": forecast_scorecard.render_basic,
    "Качество прогнозов": forecast_scorecard.render_quality,
    "Дивиденды": human_portfolio.render_dividends,
    "Риски": human_portfolio.render_risks,
    "Сценарии": human_portfolio.render_scenarios,
    "Обновить данные": human_portfolio.render_update,
    "История обновлений": forecast_scorecard.render_update_history,
}
advanced = st.sidebar.toggle("Расширенный режим", value=False)
if advanced:
    groups = group_advanced_pages(advanced_pages)
    group = st.sidebar.selectbox("Раздел", groups)
    selected = st.sidebar.selectbox("Страница", groups[group])
    groups[group][selected]()
else:
    pages = navigation_pages(basic_pages, advanced_pages)
    selected = st.sidebar.radio("Навигация", pages, index=0)
    pages[selected]()
