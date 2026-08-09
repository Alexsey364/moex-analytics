"""Human-facing data inventory and auditable decision explanations."""

import pandas as pd
import streamlit as st

from moex_analytics.database import connection, database_path
from moex_analytics.transparency import (
    data_inventory,
    explain_current_decision,
    instrument_data_passport,
    update_receipt,
)


def render_data() -> None:
    st.header("Мои данные")
    st.caption("Что программа действительно хранит, когда проверяла и насколько это свежее.")
    with connection(read_only=False) as con:
        inventory = data_inventory(con, database_path(), save=True)
    totals = inventory["totals"]
    cards = st.columns(4)
    cards[0].metric("Торговых строк", f"{totals['eod_rows']:,}".replace(",", " "))
    cards[1].metric("Исторических бумаг", totals["historical_securities"])
    cards[2].metric("Фундаментальных значений", totals["fundamental_validated_values"])
    cards[3].metric("Прогнозов", totals["forecasts"])
    size = inventory["storage"].get("duckdb_bytes")
    st.metric("Размер DuckDB", f"{size / 1024**3:.2f} GB" if size else "не рассчитан")
    st.subheader("Свежесть наборов")
    st.dataframe(pd.DataFrame(inventory["freshness"]), use_container_width=True, hide_index=True)
    with st.expander("Полный inventory"):
        st.json(totals)


def render_trace() -> None:
    st.header("Почему программа сделала такой вывод?")
    secid = st.selectbox("Бумага", ["SBERP", "LKOH", "MTSS", "X5", "TATNP", "TRNFP", "PHOR", "MOEX"])
    with connection(read_only=False) as con:
        trace = explain_current_decision(con, secid)
        passport = instrument_data_passport(con, secid)
    st.subheader(f"{secid}: {trace['final_status']}")
    st.caption(f"Cutoff: {trace['cutoff']} · checked {trace['blocks_checked']} · used {trace['blocks_used']}")
    positive, negative, neutral = st.columns(3)
    positive.success(
        "🟢 ЗА\n\n" + "\n\n".join(trace["summary"]["positive"] or ["Нет подтверждённых положительных блоков"])
    )
    negative.error(
        "🔴 ПРОТИВ\n\n"
        + "\n\n".join(trace["summary"]["negative"] or ["Нет подтверждённых отрицательных блоков"])
    )
    neutral.warning("🟡 НЕЙТРАЛЬНО\n\nНедостаточные блоки показаны ниже")
    st.warning(
        "Модель ещё не имеет достаточной выборки созревших live-прогнозов. Числовая вероятность скрыта."
    )
    with st.expander("Какие данные программа посмотрела"):
        st.json(passport)
    with st.expander("Что программа НЕ использовала и почему"):
        st.dataframe(pd.DataFrame(trace["excluded"]), use_container_width=True, hide_index=True)


def render_update_receipt() -> None:
    st.header("Последний чек обновления")
    with connection(read_only=False) as con:
        receipt = update_receipt(con)
    if receipt["status"] != "available":
        st.info(receipt["message"])
        return
    row = receipt["receipt"]
    st.success(
        "ОБНОВЛЕНИЕ ЗАВЕРШЕНО" if row.get("status") not in {"failed", "error"} else "ОБНОВЛЕНИЕ С ОШИБКОЙ"
    )
    cols = st.columns(5)
    cols[0].metric("Новых строк", row.get("rows_inserted", 0))
    cols[1].metric("Обновлено", row.get("rows_revised", 0))
    cols[2].metric("Запросов", row.get("http_requests", 0))
    cols[3].metric("Ошибок", row.get("errors", 0))
    cols[4].metric("Время", f"{row.get('duration_seconds', 0):.1f} c")
    st.json(row)


def render_update() -> None:
    """Keep the established safe updater and add its auditable receipt."""
    from moex_analytics.dashboard.pages import human_portfolio

    human_portfolio.render_update()
    st.divider()
    render_update_receipt()
