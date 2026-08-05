import streamlit as st

from ...analogues import calculate_all as calculate_analogues
from ...canonical import build_canonical
from ...cli import download_one
from ...config import load_instruments, load_segments
from ...data_quality import record_issues
from ...database import (
    connection,
    init_database,
    insert_dividends,
    upsert_segments,
)
from ...features import calculate_all as calculate_features
from ...forward_returns import calculate_all as calculate_forward_returns
from ...market_regime import calculate_all as calculate_regimes
from ...moex_client import MoexClient
from ...returns import calculate_all
from ...scoring import calculate_all as calculate_scores
from ..state import run_update_steps


def _connection_check():
    return MoexClient().discover("IMOEX")["name"]


def _quotes(tickers):
    return {ticker: download_one(ticker, None, None) for ticker in tickers}


def _dividends(tickers):
    client = MoexClient()
    with connection() as con:
        return {ticker: insert_dividends(con, client.dividends(ticker)) for ticker in tickers}


def _canonical():
    with connection() as con:
        upsert_segments(con, load_segments())
        return build_canonical(con)


def _returns():
    with connection() as con:
        return calculate_all(con)


def _quality():
    with connection() as con:
        return record_issues(con)


def _analytics(action):
    with connection() as con:
        return action(con)


def full_update_steps(tickers):
    return [
        ("Проверка соединения", _connection_check),
        ("Котировки", lambda: _quotes(tickers)),
        ("Дивиденды", lambda: _dividends(tickers)),
        ("Канонический ряд", _canonical),
        ("Доходности", _returns),
        ("Контроль качества", _quality),
        ("Факторы", lambda: _analytics(calculate_features)),
        ("Режимы рынка", lambda: _analytics(calculate_regimes)),
        ("Будущие доходности", lambda: _analytics(calculate_forward_returns)),
        ("Исторические аналоги", lambda: _analytics(calculate_analogues)),
        ("Итоговые оценки", lambda: _analytics(calculate_scores)),
    ]


def render():
    st.header("Обновление данных")
    init_database()
    choices = [item["secid"] for item in load_instruments()]
    tickers = st.multiselect("Инструменты", choices, default=choices)
    st.caption("По умолчанию загружаются только даты после последней записи.")
    actions = {
        "Проверить соединение с MOEX": [("Проверка соединения", _connection_check)],
        "Обновить котировки": [("Котировки", lambda: _quotes(tickers))],
        "Обновить дивиденды": [("Дивиденды", lambda: _dividends(tickers))],
        "Перестроить канонический ряд": [("Канонический ряд", _canonical)],
        "Пересчитать доходности": [("Доходности", _returns)],
        "Проверить качество данных": [("Контроль качества", _quality)],
        "Выполнить полное обновление": full_update_steps(tickers),
    }
    for label, steps in actions.items():
        if st.button(label, disabled=not tickers and "соединение" not in label.lower()):
            progress = st.progress(0, text="Запуск")
            total_steps = len(steps)

            def on_step(name, progress=progress, total=total_steps):
                progress.progress(
                    len(st.session_state.get("done_steps", [])) / max(total, 1),
                    text=name,
                )

            result = run_update_steps(steps, on_step)
            progress.progress(1.0, text="Завершено" if not result.error else "Ошибка")
            if result.error:
                st.error(f"Шаг «{result.error_step}»: {result.error}")
            else:
                st.success("Операция завершена")
                st.cache_data.clear()
            st.json(result.outputs)
