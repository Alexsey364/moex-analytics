"""Read-only Stage 70 news and market-impact dashboard."""

from __future__ import annotations

import json

import streamlit as st

from moex_analytics.dashboard.data_access import read_connection


def load_news_view(con) -> dict:
    try:
        items = con.execute("SELECT published_at,headline,event_type,entities_json,tone,story_id "
            "FROM news_items ORDER BY published_at DESC LIMIT 30").fetchall()
        run = con.execute("SELECT status,rows_available,validated_variants,production_weight "
            "FROM news_research_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        reactions = con.execute("SELECT secid,horizon,market_return,persistence,interpretation "
            "FROM news_reaction_memory ORDER BY anchor_date DESC LIMIT 30").fetchall()
    except Exception:
        return {"items": [], "reactions": [], "research": None}
    return {"items": items, "reactions": reactions, "research": run}


def render() -> None:
    st.header("Что сейчас двигает рынок")
    st.caption("Только официальные источники; тон описательный и не является торговым сигналом.")
    with read_connection() as con:
        view = load_news_view(con)
    if not view["items"]:
        st.info("Свежие подтверждённые новости пока не загружены.")
        return
    run = view["research"]
    if not run or run[0] == "requires_more_history":
        st.warning("Новостная история ещё недостаточна для подтверждённого прогнозного вывода.")
    else:
        st.info(f"Исследовательский статус: {run[0]}; production weight: {run[3]:.0%}")
    for published, headline, event_type, entities, tone, _story in view["items"][:12]:
        names = ", ".join(json.loads(entities or "[]")) or "рынок в целом"
        with st.container(border=True):
            st.markdown(f"**{headline}**")
            st.caption(f"{published} · {event_type} · {names} · {tone}")
    st.subheader("Фактическая реакция после момента доступности")
    if not view["reactions"]:
        st.info("Зрелых торговых исходов пока нет; старые цены не приписываются новым новостям.")
    else:
        st.dataframe(view["reactions"], use_container_width=True)
