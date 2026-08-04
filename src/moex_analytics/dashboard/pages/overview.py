from datetime import date, timedelta

import streamlit as st

from ..charts import price_chart, return_chart
from ..data_access import database_summary, instrument_summary, prices, returns
from ..formatting import format_date, format_number


def render():
    st.header("Обзор")
    summary = database_summary()
    cols = st.columns(5)
    values = [
        ("Инструментов", summary["instruments"]),
        ("Канонических строк", summary["canonical_rows"]),
        ("Начало", format_date(summary["date_from"])),
        ("Конец", format_date(summary["date_to"])),
        ("Проблем качества", summary["issues"]),
    ]
    for col, (label, value) in zip(cols, values, strict=True):
        col.metric(label, format_number(value) if isinstance(value, int) else value)
    period = st.selectbox("Период IMOEX", ["1 год", "3 года", "5 лет", "Вся история"])
    years = {"1 год": 1, "3 года": 3, "5 лет": 5}.get(period)
    start = date.today() - timedelta(days=365 * years) if years else None
    frame = prices("IMOEX", start)
    log = st.checkbox("Логарифмическая шкала")
    st.plotly_chart(price_chart(frame, log_scale=log), use_container_width=True)
    total = returns("IMOEX", start)
    if not total.empty and st.checkbox("Показать индекс полной доходности"):
        st.plotly_chart(return_chart(total), use_container_width=True)
    table = instrument_summary()
    st.dataframe(
        table.rename(
            columns={
                "ticker": "Тикер",
                "name": "Название",
                "first_date": "Первая дата",
                "last_date": "Последняя дата",
                "trading_days": "Торговых дней",
                "last_price": "Последняя цена",
                "change_1d": "За день",
                "change_20d": "За 20 дней",
                "dividends": "Дивидендов",
                "issues": "Проблем",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
