from datetime import date, timedelta

import streamlit as st

from ..charts import price_chart, return_chart
from ..data_access import dividends, instrument_summary, prices, returns, segments


def render():
    st.header("Инструмент")
    secid = st.selectbox("Инструмент", ["IMOEX", "SBER", "LKOH", "GAZP"])
    period = st.selectbox("Период", ["3 месяца", "1 год", "3 года", "5 лет", "Вся история"])
    days = {"3 месяца": 92, "1 год": 365, "3 года": 1095, "5 лет": 1825}.get(period)
    start = date.today() - timedelta(days=days) if days else None
    info = instrument_summary()
    selected = info[info.ticker == secid]
    if not selected.empty:
        row = selected.iloc[0]
        cols = st.columns(4)
        cols[0].metric("Тикер", secid)
        cols[1].metric("Последняя цена", f"{row.last_price:,.2f}")
        cols[2].metric("Торговых дней", f"{row.trading_days:,.0f}")
        cols[3].metric("Проблем данных", f"{row.issues:,.0f}")
        st.caption(f"{row['name']} · {row.first_date:%d.%m.%Y} — {row.last_date:%d.%m.%Y}")
    chart_type = st.radio("График", ["Линия", "Свечи"], horizontal=True)
    log = st.checkbox("Логарифмическая шкала", key="instrument_log")
    frame = prices(secid, start)
    if frame.empty:
        st.info("Для выбранного периода данных недостаточно.")
        return
    st.plotly_chart(price_chart(frame, chart_type == "Свечи", log), use_container_width=True)
    total = returns(secid, start)
    if not total.empty:
        st.subheader("Доходности")
        st.line_chart(total.set_index("trade_date")[["price_return"]])
        st.plotly_chart(return_chart(total), use_container_width=True)
        close = frame.set_index("trade_date").close
        st.line_chart((close / close.iloc[0] - 1).rename("Накопленная ценовая доходность"))
        st.line_chart((close / close.cummax() - 1).rename("Просадка"))
    st.subheader("Дивиденды")
    st.dataframe(dividends(secid), use_container_width=True, hide_index=True)
    st.caption("MOEX ISS не предоставляет даты объявления и фактической выплаты.")
    st.subheader("Исторические торговые сегменты")
    st.dataframe(segments(secid), use_container_width=True, hide_index=True)
