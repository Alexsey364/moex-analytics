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
    with connection(read_only=True) as con:
        inventory = data_inventory(con, database_path(), save=False)
        try:
            run_id = con.execute("SELECT run_id FROM current_quality_runs ORDER BY created_at DESC "
                                 "LIMIT 1").fetchone()[0]
            current_freshness = con.execute("SELECT dataset_family,latest_data_date,"
                "expected_latest_date,status,reason FROM dataset_freshness_current WHERE run_id=? "
                "ORDER BY dataset_family", [run_id]).df()
            portfolio_quality = con.execute("SELECT secid,price_data,market_context,ranking,"
                "fundamentals,corporate_actions,overall,reason FROM portfolio_quality_current "
                "WHERE run_id=? ORDER BY secid", [run_id]).df()
            quality_counts = con.execute("SELECT status,critical,warnings FROM current_quality_runs "
                                         "WHERE run_id=?", [run_id]).fetchone()
        except Exception:
            current_freshness = portfolio_quality = pd.DataFrame()
            quality_counts = ("unavailable", 0, 0)
    totals = inventory["totals"]
    cards = st.columns(3)
    cards[0].metric("Каталог", f"{totals['catalog_securities']:,} бумаг".replace(",", " "))
    cards[1].metric("С историей торгов", f"{totals['securities_with_eod_history']:,} бумаг")
    cards[2].metric(
        "Всего дневных торговых наблюдений",
        f"{totals['raw_eod_rows']:,}".replace(",", " "),
    )
    st.caption(
        f"Из бумаг с историей: active {totals['active_securities_with_history']}, "
        f"inactive {totals['inactive_securities_with_history']}. "
        f"Canonical: {totals['canonical_eod_rows']:,}; "
        f"текущий портфель: {totals['portfolio_eod_rows']:,}.".replace(",", " ")
    )
    st.subheader("Прогнозы и фактическая проверка")
    forecast_cards = st.columns(5)
    forecast_cards[0].metric("Прогнозов всего", totals["forecasts"])
    forecast_cards[1].metric("Ожидают срока", totals["pending_forecasts"])
    forecast_cards[2].metric("Созрело", totals["matured_forecasts"])
    forecast_cards[3].metric("Pending outcome records", totals["pending_outcome_records"])
    forecast_cards[4].metric("Фактически оценено", totals["evaluated_forecasts"])
    if totals["matured_forecasts"] == 0:
        st.info(
            f"{totals['pending_outcome_records']} outcome-записей являются ожидающими, "
            "а не уже проверенными результатами."
        )
    size = inventory["storage"].get("duckdb_bytes")
    st.metric("Размер DuckDB", f"{size / 1024**3:.2f} GB" if size else "не рассчитан")
    st.subheader("Качество текущего анализа")
    st.write(f"Статус: {quality_counts[0]} · критических: {quality_counts[1]} · "
             f"предупреждений: {quality_counts[2]}")
    st.subheader("Свежесть по семействам данных")
    st.dataframe(current_freshness, use_container_width=True, hide_index=True)
    st.subheader("Мои 9 бумаг")
    st.dataframe(portfolio_quality, use_container_width=True, hide_index=True)
    with st.expander("Историческая техническая свежесть (Advanced audit)"):
        st.dataframe(pd.DataFrame(inventory["freshness"]), use_container_width=True, hide_index=True)
    with st.expander("Полный inventory"):
        st.json(totals)


def render_trace() -> None:
    st.header("Почему программа сделала такой вывод?")
    secid = st.selectbox("Бумага", ["SBERP", "LKOH", "MTSS", "X5", "TATNP", "TRNFP", "PHOR", "MOEX"])
    with connection(read_only=False) as con:
        trace = explain_current_decision(con, secid)
        passport = instrument_data_passport(con, secid)
    st.subheader(secid)
    st.markdown(f"### Инвестиционная оценка: {trace['investment_view']['label']}")
    st.markdown(
        f"### Портфельное ограничение: {trace['portfolio_allocation_view']['label']}"
    )
    st.caption(
        f"Cutoff: {trace['cutoff']} · checked {trace['blocks_checked']} · "
        f"used {trace['blocks_used']} · влияют {len(trace['influential'])} · "
        f"informational {len(trace['informational'])} · excluded {len(trace['excluded'])}"
    )
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
    with st.expander("Какие блоки влияют на инвестиционный вывод"):
        st.write(trace["influential"])
        st.caption("Информационный контекст, не меняющий статус:")
        st.write(trace["informational"])


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
    from moex_analytics import update_monitor
    from moex_analytics.dashboard.pages import human_portfolio

    state = update_monitor.recover_interrupted()
    if state:
        health = update_monitor.health(state)
        icon = {"ACTIVE": "🟢", "SLOW": "🟡", "STALLED": "🔴"}.get(health, "⚪")
        st.subheader("Live progress")
        st.write(f"{icon} {state.get('status')} — {state.get('current_source') or '—'} / "
                 f"{state.get('current_stage') or '—'}")
        total, done = state.get("items_total"), state.get("items_done", 0)
        if total:
            st.progress(min(1.0, done / total), text=f"{done} / {total}")
        cols = st.columns(4)
        cols[0].metric("Requests", state.get("requests_completed", 0))
        cols[1].metric("New rows", state.get("rows_inserted", 0))
        cols[2].metric("Errors", state.get("errors", 0))
        eta = update_monitor.eta_seconds(state)
        cols[3].metric("ETA", f"~{int(eta)} sec" if eta is not None else "unavailable")
        if state.get("status") in {"starting", "running", "waiting_source", "retrying"}:
            if st.button("Остановить после текущего запроса"):
                update_monitor.request_cancel()
                st.warning("Запрос на безопасную остановку сохранён.")
        stages = state.get("stages", [])
        if stages:
            st.dataframe(stages, use_container_width=True, hide_index=True)
        with st.expander("Последние события"):
            for event in state.get("events", [])[-30:]:
                st.text(f"{event.get('at', '')}  {event.get('message', '')}")

    human_portfolio.render_update()
    st.divider()
    render_update_receipt()
