"""Visual Historical Memory 5.0 BASIC and technical Advanced views."""

from __future__ import annotations

import json
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from moex_analytics.dashboard.data_access import read_connection
from moex_analytics.market_memory import market_memory_status

NAMES = {
    "X5": "X5",
    "SBERP": "Сбербанк ап",
    "LKOH": "ЛУКОЙЛ",
    "LSNGP": "Россети Ленэнерго ап",
    "MTSS": "МТС",
    "TRNFP": "Транснефть ап",
    "TATNP": "Татнефть ап",
    "PHOR": "ФосАгро",
    "MOEX": "Московская биржа",
}
HORIZONS = {"1 неделя": 5, "1 месяц": 20, "3 месяца": 60, "6 месяцев": 120, "1 год": 250}
MODES = {
    "График акции": "price_path",
    "Акция + рынок": "stock_market",
    "Полная рыночная ситуация": "full_state",
}
STATE_ICONS = {"похоже": "🟢", "частично": "🟡", "не использовалось": "⚪", "отличается": "🟠"}


@st.cache_data(ttl=300, show_spinner=False)
def _load_snapshot(instrument: str, horizon: int, mode: str) -> dict[str, Any] | None:
    with read_connection() as con:
        row = con.execute(
            """SELECT cutoff,sample,status,reason,current_path_json,analog_paths_json,bands_json,
            cards_json,summary_json,why_json,scenarios_json,method,prehistory_window
            FROM visual_memory_snapshots WHERE run_id=(SELECT run_id FROM visual_memory_runs
            WHERE status='completed' ORDER BY created_at DESC LIMIT 1)
            AND instrument=? AND horizon=? AND comparison_mode=?""",
            [instrument, horizon, mode],
        ).fetchone()
    if not row:
        return None
    keys = (
        "cutoff",
        "sample",
        "status",
        "reason",
        "current",
        "analogs",
        "bands",
        "cards",
        "summary",
        "why",
        "scenarios",
        "method",
        "window",
    )
    result = dict(zip(keys, row, strict=True))
    for key in ("current", "analogs", "bands", "cards", "summary", "why", "scenarios"):
        result[key] = json.loads(result[key] or "[]")
    return result


def _chart(snapshot: dict[str, Any], normalized: bool) -> go.Figure:
    value_key = "normalized" if normalized else "real_price"
    axis_title = "Индекс, T0 = 100" if normalized else "Реальная историческая цена, ₽"
    figure = go.Figure()
    for path in snapshot["analogs"]:
        points = [point for point in path["points"] if point.get(value_key) is not None]
        figure.add_trace(
            go.Scatter(
                x=[point["relative_session"] for point in points],
                y=[point[value_key] for point in points],
                mode="lines",
                line={"width": 2 if path["rank"] == 1 else 1},
                opacity=0.65 if path["rank"] == 1 else 0.25,
                name=f"{path['date']} · аналог {path['rank']}",
                customdata=[[path["date"], path["similarity"]] for _ in points],
                hovertemplate="Дата аналога: %{customdata[0]}<br>Сходство: %{customdata[1]:.2f}<br>"
                "Сессия: %{x}<br>Значение: %{y:.2f}<extra></extra>",
            )
        )
    if normalized and snapshot["bands"]:
        x = [row["relative_session"] for row in snapshot["bands"]]
        for low, high, color, name in (
            ("q10", "q90", "rgba(100,116,139,.12)", "Диапазон 10–90%"),
            ("q25", "q75", "rgba(59,130,246,.18)", "Диапазон 25–75%"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=[row[low] for row in snapshot["bands"]],
                    mode="lines",
                    line={"width": 0},
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=[row[high] for row in snapshot["bands"]],
                    mode="lines",
                    line={"width": 0},
                    fill="tonexty",
                    fillcolor=color,
                    name=name,
                    hoverinfo="skip",
                )
            )
        figure.add_trace(
            go.Scatter(
                x=x,
                y=[row["median"] for row in snapshot["bands"]],
                mode="lines",
                line={"color": "#2563eb", "width": 3, "dash": "dash"},
                name="Медиана реальных аналогов",
            )
        )
    current = [point for point in snapshot["current"] if point.get(value_key) is not None]
    figure.add_trace(
        go.Scatter(
            x=[point["relative_session"] for point in current],
            y=[point[value_key] for point in current],
            mode="lines",
            line={"color": "#111827", "width": 5},
            name="СЕЙЧАС",
            hovertemplate="Текущая траектория<br>Сессия: %{x}<br>Значение: %{y:.2f}<extra></extra>",
        )
    )
    figure.add_vline(x=0, line_width=2, line_dash="dash", line_color="#dc2626")
    figure.add_annotation(
        x=0, y=1.08, yref="paper", text="СЕГОДНЯ", showarrow=False, font={"color": "#dc2626", "size": 14}
    )
    figure.add_annotation(
        x=max(1, snapshot["window"] // 2),
        y=1.02,
        yref="paper",
        text="Что происходило после похожих ситуаций",
        showarrow=False,
    )
    figure.update_layout(
        height=570,
        margin={"l": 30, "r": 20, "t": 70, "b": 40},
        hovermode="x unified",
        xaxis_title="Торговые сессии относительно T0",
        yaxis_title=axis_title,
        legend={"orientation": "h", "y": -0.18},
        template="plotly_white",
    )
    return figure


def _summary(snapshot: dict[str, Any], horizon_name: str) -> None:
    summary = snapshot["summary"]
    st.subheader("Как обычно развивались эти ситуации")
    if snapshot["sample"] < 5:
        st.info("⚪ Слишком мало действительно похожих периодов, чтобы делать сильный вывод.")
        return
    cols = st.columns(5)
    cols[0].metric("Независимых аналогов", summary["analogs"])
    cols[1].metric(f"Выше через {horizon_name.lower()}", f"{summary['above']} из {summary['analogs']}")
    cols[2].metric("Медианный результат", f"{summary['median']:+.1%}")
    cols[3].metric("Типичный диапазон", f"{summary['q25']:+.1%} … {summary['q75']:+.1%}")
    cols[4].metric("Широкий диапазон", f"{summary['q10']:+.1%} … {summary['q90']:+.1%}")
    if summary.get("median_drawdown") is not None:
        st.caption(f"Типичная максимальная просадка: {summary['median_drawdown']:.1%}.")
    st.caption("Это подсчёт реальных исторических эпизодов, а не вероятность будущего роста.")


def _cards(snapshot: dict[str, Any]) -> None:
    st.subheader("Наиболее похожие периоды")
    for card in snapshot["cards"]:
        with st.container(border=True):
            left, right = st.columns([1, 2])
            left.markdown(f"### {card['date']}")
            left.write(f"{card['similarity_label']} · сходство {card['similarity']:.2f}")
            left.write(f"Режим рынка: {'похож' if card['regime_similar'] else 'отличается'}")
            left.write(f"Сценарий: {card['scenario'] or 'не определён'}")
            returns = {int(key): value for key, value in card["returns"].items()}
            labels = {5: "1 неделя", 20: "1 месяц", 60: "3 месяца", 120: "6 месяцев", 250: "1 год"}
            right.markdown("**Что произошло потом**")
            right.write(" · ".join(f"{labels[h]}: {returns[h]:+.1%}" for h in labels if h in returns))
            if card.get("max_drawdown") is not None:
                right.write(f"Максимальная просадка: {card['max_drawdown']:.1%}")
            right.caption("Похожесть определяется только данными до исторического T0.")


def _why(snapshot: dict[str, Any]) -> None:
    st.subheader("Почему программа считает периоды похожими")
    cols = st.columns(2)
    for index, (component, state) in enumerate(snapshot["why"].items()):
        cols[index % 2].write(f"{STATE_ICONS.get(state, '⚪')} **{component}** — {state}")


def _scenarios(snapshot: dict[str, Any]) -> None:
    st.subheader("Реальные группы сценариев")
    if not snapshot["scenarios"]:
        st.info("⚪ Для выбранного среза группы сценариев недоступны.")
        return
    cols = st.columns(min(4, len(snapshot["scenarios"])))
    for index, scenario in enumerate(snapshot["scenarios"][:4]):
        with cols[index]:
            st.markdown(f"**{scenario['scenario']}**")
            st.write(f"Эпизодов: {scenario['episodes']}")
            st.write(f"Медианный итог: {scenario['median_return']:+.1%}")
            st.write(f"Типичная просадка: {scenario['median_drawdown']:.1%}")
            st.caption(f"Реальный представитель: {scenario['representative_date']}")


def _technical(instrument: str, horizon: int) -> None:
    with read_connection() as con:
        status = market_memory_status(con, ensure=False)
        if not status["latest"]:
            return
        frame = con.execute(
            """SELECT method,cutoff_date,sample,similarity,median_return,q10,q90,
            positive_fraction,median_drawdown,median_mfe,oos_value_add,status,reason
            FROM market_analog_scorecards WHERE run_id=? AND instrument=? AND horizon=?""",
            [status["latest"][0], instrument, horizon],
        ).df()
    st.dataframe(frame, use_container_width=True, hide_index=True)


def render() -> None:
    st.header("Похожие ситуации в прошлом")
    st.caption(
        "Программа ищет реальные периоды истории, когда акция и рынок находились в похожем "
        "состоянии, и показывает, что происходило после."
    )
    left, middle, right = st.columns(3)
    instrument_name = left.selectbox("Бумага", list(NAMES.values()), index=1)
    instrument = next(key for key, value in NAMES.items() if value == instrument_name)
    horizon_name = middle.selectbox(
        "Горизонт",
        list(HORIZONS),
        index=1,
        help="1 неделя = 5, месяц = 20, 3 месяца = 60, 6 месяцев = 120, год = 250 торговых сессий",
    )
    mode_name = right.radio("Что считать похожим", list(MODES), horizontal=True)
    normalized = (
        st.segmented_control("Шкала", ["Нормализовано", "Реальная цена"], default="Нормализовано")
        == "Нормализовано"
    )
    horizon, mode = HORIZONS[horizon_name], MODES[mode_name]
    snapshot = _load_snapshot(instrument, horizon, mode)
    if not snapshot:
        st.info("⚪ Совместимый завершённый snapshot ещё не рассчитан.")
        return
    st.caption(
        f"Точка сравнения T0: {snapshot['cutoff']}. Последующие данные текущей ситуации "
        "не участвуют в выборе аналогов."
    )
    if snapshot["status"] != "ready":
        st.info("⚪ Слишком мало действительно похожих независимых периодов, чтобы делать сильный вывод.")
        with st.expander("Технические детали"):
            _technical(instrument, horizon)
        return
    st.subheader("Сегодня и похожие периоды")
    st.plotly_chart(_chart(snapshot, normalized), use_container_width=True, config={"displayModeBar": False})
    _summary(snapshot, horizon_name)
    _cards(snapshot)
    _why(snapshot)
    _scenarios(snapshot)
    selected = st.selectbox("Сравнить подробно", [card["date"] for card in snapshot["cards"]])
    selected_path = next(path for path in snapshot["analogs"] if path["date"] == selected)
    with st.expander(f"СЕЙЧАС и ТОГДА — {selected}"):
        current_only = dict(snapshot, analogs=[], bands=[])
        historical_only = dict(snapshot, current=[], analogs=[selected_path], bands=[])
        a, b = st.columns(2)
        a.plotly_chart(
            _chart(current_only, normalized), use_container_width=True, config={"displayModeBar": False}
        )
        b.plotly_chart(
            _chart(historical_only, normalized), use_container_width=True, config={"displayModeBar": False}
        )
    st.subheader("Как это относится к текущему решению")
    st.info(
        "🟡 Аналоги являются research evidence block. Они уточняют диапазон и downside, "
        "но самостоятельно не меняют портфельный статус и не доказывают направление."
    )
    with st.expander("Технические детали"):
        _technical(instrument, horizon)
    st.caption("Production logic и probability gate не изменены. Синтетические траектории не используются.")


def render_advanced() -> None:
    st.header("Historical Market Memory — техническая диагностика")
    instrument = st.selectbox("Ticker", list(NAMES))
    horizon = st.selectbox("Trading sessions", list(HORIZONS.values()))
    _technical(instrument, horizon)


def render_basic_analogs(instrument: str) -> None:
    """Keep compact stock-card integration on the same precomputed evidence."""
    snapshot = _load_snapshot(instrument, 20, "price_path")
    if not snapshot or snapshot["status"] != "ready":
        st.info("⚪ Для этой бумаги пока недостаточно независимых исторических аналогов.")
        return
    summary = snapshot["summary"]
    st.subheader("Похожие ситуации в прошлом")
    st.write(
        f"Найдено {summary['analogs']} независимых эпизодов; через месяц выше T0 оказались "
        f"{summary['above']} из {summary['analogs']}. Медианный результат: {summary['median']:+.1%}."
    )
    st.caption("Это исторический подсчёт, не вероятность роста.")
