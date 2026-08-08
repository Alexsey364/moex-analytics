"""Explainable market breadth and trading-statistics pages."""

import streamlit as st

from moex_analytics.database import connection
from moex_analytics.market_history import ensure_schema


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
    cols = st.columns(4)
    cols[0].metric("Торгуемых бумаг", int(last.tradable_count))
    cols[1].metric("Растут / падают", f"{int(last.advancing)} / {int(last.declining)}")
    cols[2].metric("Выше SMA50", f"{100 * last.above_sma50 / max(last.tradable_count, 1):.0f}%")
    cols[3].metric("Оборот", f"{last.total_turnover / 1e9:.1f} млрд ₽")
    if not state.empty:
        st.subheader(str(state.iloc[0].state_label).replace("_", " ").title())
        st.caption(
            "Профиль объясняется breadth, turnover, trend и dispersion; "
            "нормализация использует только прошлые сессии."
        )
    st.line_chart(breadth.set_index("trade_date")[["advancing", "declining", "tradable_count"]])
    st.caption(f"Покрытие: {coverage[0]} бумаг, {coverage[1]} строк, {coverage[2]} — {coverage[3]}.")


def render_trading_statistics() -> None:
    st.header("Статистика торгов")
    coverage, breadth, state, liquidity = _tables()
    st.write({"securities": coverage[0], "rows": coverage[1], "from": coverage[2], "to": coverage[3]})
    tabs = st.tabs(["Liquidity", "Breadth 2.0", "Market state", "Методология"])
    with tabs[0]:
        st.dataframe(liquidity, use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(breadth, use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(state, use_container_width=True, hide_index=True)
    with tabs[3]:
        st.markdown(
            "Tradable-on-date означает наличие торгов, не членство в индексе. "
            "Дублирующие доски исключаются явным правилом максимального оборота. "
            "Состояние рынка — research, не production-сигнал."
        )
