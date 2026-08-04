import streamlit as st

from ..data_access import board_conflicts, quality_issues


def render():
    st.header("Качество данных")
    secid = st.selectbox("Инструмент", ["Все", "IMOEX", "SBER", "LKOH", "GAZP"])
    selected = None if secid == "Все" else secid
    frame = quality_issues(selected)
    st.metric("Найдено проблем", len(frame))
    if frame.empty:
        st.success("По выбранному фильтру проблем нет.")
        return
    issue_types = ["Все", *sorted(frame.issue_type.unique())]
    issue = st.selectbox("Тип проблемы", issue_types)
    if issue != "Все":
        frame = frame[frame.issue_type == issue]
    st.bar_chart(frame.groupby("issue_type").size())
    st.dataframe(
        frame.rename(
            columns={
                "trade_date": "Дата",
                "secid": "Тикер",
                "issue_type": "Тип",
                "description": "Описание",
                "detected_at": "Обнаружено",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
    if selected:
        st.subheader("Конфликты торговых досок")
        conflicts = board_conflicts(selected)
        st.dataframe(conflicts, use_container_width=True, hide_index=True)
        st.subheader("Пропущенные обязательные значения")
        st.dataframe(frame[frame.issue_type == "missing_required"], hide_index=True)
        st.subheader("Цены вне диапазона low–high")
        st.dataframe(
            frame[frame.issue_type.isin(["open_outside_range", "close_outside_range"])], hide_index=True
        )
