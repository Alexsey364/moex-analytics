"""Explainable market breadth and trading-statistics pages."""

import streamlit as st

from moex_analytics.dashboard.investor_visuals import breadth_figure
from moex_analytics.dashboard.visual_semantics import accessible_label, token_for
from moex_analytics.database import connection
from moex_analytics.market_history import ensure_schema


@st.cache_data(ttl=60, show_spinner=False)
def _tables():
    with connection(read_only=False) as con:
        ensure_schema(con)
        coverage = con.execute(
            "SELECT count(distinct secid),count(*),min(trade_date),max(trade_date) FROM moex_equity_eod"
        ).fetchone()
        breadth = con.execute("SELECT * FROM market_breadth_daily ORDER BY trade_date DESC LIMIT 250").df()
        state = con.execute("SELECT * FROM market_state_daily ORDER BY trade_date DESC LIMIT 250").df()
        liquidity = con.execute(
            """SELECT trade_date,secid,turnover_20,volume_20,trades_20,
            liquidity_percentile,amihud FROM equity_liquidity_daily
            QUALIFY row_number() over(PARTITION BY secid ORDER BY trade_date DESC)=1
            ORDER BY turnover_20 DESC"""
        ).df()
    return coverage, breadth, state, liquidity


def render_market_state() -> None:
    st.header("Состояние рынка")
    coverage, breadth, state, _ = _tables()
    if breadth.empty:
        st.info("Недостаточно загруженной истории для расчёта ширины рынка.")
        return
    last = breadth.iloc[0]
    period = st.radio("Период", ("1М", "3М", "1Г"), horizontal=True, index=1)
    window = {"1М": 23, "3М": 66, "1Г": 250}[period]
    cols = st.columns(4)
    cols[0].metric("Торгуемых бумаг", int(last.tradable_count))
    cols[1].metric("Растут / падают", f"{int(last.advancing)} / {int(last.declining)}")
    cols[2].metric("Выше SMA50", f"{100 * last.above_sma50 / max(last.tradable_count, 1):.0f}%")
    cols[3].metric("Оборот", f"{last.total_turnover / 1e9:.1f} млрд ₽")
    if not state.empty:
        current_state = str(state.iloc[0].state_label)
        state_token = token_for("negative" if "stress" in current_state.lower() else "neutral")
        st.subheader(f"{state_token.symbol} {current_state.replace('_', ' ').title()}")
        st.caption(
            "Профиль объясняется breadth, turnover, trend и dispersion; "
            "нормализация использует только прошлые сессии."
        )
    sma200 = last.above_sma200 / max(last.tradable_count, 1)
    status = "negative" if sma200 < 0.4 else "mixed" if sma200 < 0.6 else "positive"
    st.metric("Рынок выше SMA200", f"{sma200:.0%}")
    st.caption(accessible_label(status))
    st.plotly_chart(breadth_figure(breadth.head(window)), use_container_width=True, key="market_breadth")
    if not state.empty:
        timeline = state.head(window).sort_values("trade_date")
        st.subheader("Сохранённая временная шкала режимов")
        st.dataframe(timeline[["trade_date", "state_label"]].T, use_container_width=True)
        st.caption("Режимы читаются из market_state_daily и не раскрашиваются вручную.")
    st.caption(f"Покрытие: {coverage[0]} бумаг, {coverage[1]} строк, {coverage[2]} — {coverage[3]}.")


def render_trading_statistics() -> None:
    st.header("Статистика торгов")
    coverage, breadth, state, liquidity = _tables()
    st.write({"securities": coverage[0], "rows": coverage[1], "from": coverage[2], "to": coverage[3]})
    tabs = st.tabs(["Liquidity", "Breadth 2.0", "Market state", "Batch history", "Методология"])
    with tabs[0]:
        st.dataframe(liquidity, use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(breadth, use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(state, use_container_width=True, hide_index=True)
    with tabs[3]:
        with connection(read_only=False) as con:
            ensure_schema(con)
            batches = con.execute("SELECT * FROM market_history_batch_runs ORDER BY finished_at DESC").df()
            requests = con.execute(
                """SELECT run_id,count(*) requests,sum(rows_received) rows_received,
                avg(duration_seconds) average_request_seconds,
                count(*) filter(where status!='completed') errors
                FROM market_history_requests GROUP BY run_id ORDER BY run_id DESC"""
            ).df()
        st.dataframe(batches, use_container_width=True, hide_index=True)
        st.dataframe(requests, use_container_width=True, hide_index=True)
    with tabs[4]:
        st.markdown(
            "Tradable-on-date означает наличие торгов, не членство в индексе. "
            "Дублирующие доски исключаются явным правилом максимального оборота. "
            "Состояние рынка — research, не production-сигнал."
        )
