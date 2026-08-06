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
from moex_analytics.dashboard.pages import (
    analytics,
    critical_data,
    data_quality,
    database_status,
    deep_backfill,
    fundamentals,
    instrument,
    macro,
    methodology,
    overview,
    predictive_foundation,
    sber_decision,
    sber_intelligence,
    sber_operational,
    unblocked_experiment,
    update_data,
)
from moex_analytics.database import database_path, init_database

st.set_page_config(page_title="Аналитика рынка MOEX", layout="wide")
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

pages = {
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
selected = st.sidebar.radio("Навигация", pages)
pages[selected]()
