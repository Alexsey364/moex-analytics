"""Stage 90 compact current briefing and immutable archive."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from moex_analytics.dashboard.data_access import read_connection


@st.cache_data(ttl=60, show_spinner=False)
def _briefings() -> list[dict]:
    try:
        with read_connection() as con:
            rows = con.execute(
                """SELECT briefing_id,cutoff,snapshot_id,payload_json,markdown_text,html_text,
                previous_briefing_id FROM daily_investor_briefings
                ORDER BY cutoff DESC,created_at DESC"""
            ).fetchall()
    except Exception:
        rows = []
    return [
        {
            "id": row[0],
            "cutoff": row[1],
            "snapshot_id": row[2],
            "payload": json.loads(row[3]),
            "markdown": row[4],
            "html": row[5],
            "previous": row[6],
        }
        for row in rows
    ]


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1%}"


def render() -> None:
    st.header("Ежедневный инвестиционный обзор")
    briefings = _briefings()
    if not briefings:
        st.info("⚪ Единый daily briefing ещё не сформирован.")
        return
    labels = {f"{row['cutoff']} · {row['id'][:8]}": row for row in briefings}
    selected_label = st.selectbox("Архив", list(labels), index=0)
    briefing = labels[selected_label]
    payload = briefing["payload"]
    market, money, live = payload["market"], payload["new_money"], payload["live"]
    st.caption(
        f"Анализ рынка и портфеля по состоянию на {payload['cutoff']} · "
        f"snapshot {payload['snapshot_id']} · {payload['compatibility']}"
    )
    columns = st.columns(6)
    columns[0].metric("Актуальность", payload["fast_components"])
    columns[1].metric("Рынок", market["state"])
    columns[2].metric("20 сессий", _pct(market["return_20"]))
    columns[3].metric("Просадка", _pct(market["drawdown"]))
    columns[4].metric("Новые 100 тыс.", f"резерв {money.get('reserve', 0):,.0f} ₽")
    columns[5].metric("Live", f"{live['matured']} / {live['total']}")
    material = [row for row in payload["changes"] if row["material"]]
    st.subheader("Что изменилось после прошлого обновления")
    if material:
        for row in material:
            st.write(f"**{row['secid']} · {row['state']}** — {'; '.join(row['reasons'])}")
    else:
        st.caption("→ Материальных изменений нет или это первый сохранённый briefing.")
    st.subheader("Мои 9 акций")
    frame = pd.DataFrame(payload["verdicts"])
    if not frame.empty:
        st.dataframe(frame, hide_index=True, use_container_width=True)
    left, right = st.columns(2)
    with left:
        st.subheader("На что сегодня обратить внимание")
        attention = sorted(payload["verdicts"], key=lambda row: "не увелич" not in row["status"].lower())
        for row in attention[:3]:
            st.write(f"• **{row['secid']}** — {row['status']}; риск: {row['risk']}")
        st.subheader("На что сейчас похож рынок")
        for analog in payload["analogs"]:
            st.write(f"• {analog['date']} · поддержка {analog['support']} бумаг")
    with right:
        st.subheader("Исторические сценарии")
        for scenario in payload["scenarios"]:
            st.write(f"• **{scenario['label']}** — {scenario['frequency']}; IMOEX {_pct(scenario['return'])}")
        st.subheader("Новые деньги")
        st.write(f"{money.get('status', 'unavailable')}: {money.get('reason', '')}")
    if payload["news"]:
        with st.expander("Только material active news context"):
            for story in payload["news"]:
                st.write(f"• {story['headline']} · {story['event_type']}")
    download_left, download_right = st.columns(2)
    download_left.download_button(
        "Сохранить Markdown",
        briefing["markdown"],
        file_name=f"moex_briefing_{payload['cutoff']}.md",
        mime="text/markdown",
    )
    download_right.download_button(
        "Сохранить HTML",
        briefing["html"],
        file_name=f"moex_briefing_{payload['cutoff']}.html",
        mime="text/html",
    )
    st.caption(
        f"Live: проверено {live['matured']}, ожидается {live['pending']}. "
        "Research-only; probability gate не ослаблен."
    )
