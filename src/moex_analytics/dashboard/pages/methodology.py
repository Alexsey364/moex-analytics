import streamlit as st

from ...config import PROJECT_ROOT


def render():
    st.header("Методология")
    for name in ("data_methodology.md", "dividend_methodology.md", "historical_boards.md"):
        path = PROJECT_ROOT / "docs" / name
        if path.exists():
            st.markdown(path.read_text(encoding="utf-8"))
    st.warning(
        "Приложение не прогнозирует рынок. Исторические результаты не гарантируют "
        "будущих результатов и не являются инвестиционной рекомендацией."
    )
