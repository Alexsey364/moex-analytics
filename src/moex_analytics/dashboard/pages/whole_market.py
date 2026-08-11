"""Human-readable Stage 79 whole-market predictive dashboard."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from moex_analytics.conditioned_stock_forecasting.core import SECTOR_MAP
from moex_analytics.dashboard.data_access import read_connection, table_exists


def market_reasons(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return no more than three positive and three cautionary facts."""
    positive: list[str] = []
    caution: list[str] = []
    ret20 = state.get("return_20")
    drawdown = state.get("drawdown")
    volatility = state.get("realized_vol20")
    if ret20 is not None:
        (positive if ret20 > 0 else caution).append(f"Динамика за 20 сессий: {ret20:+.1%}")
    if drawdown is not None:
        (positive if drawdown > -0.05 else caution).append(f"Просадка от максимума: {drawdown:.1%}")
    if volatility is not None:
        (positive if volatility < 0.2 else caution).append(f"Реализованная волатильность: {volatility:.1%}")
    return positive[:3], caution[:3]


@st.cache_data(ttl=60, show_spinner=False)
def load_dashboard_payload() -> dict[str, Any]:
    with read_connection() as con:
        required = {"whole_market_state_runs", "whole_market_state_daily", "whole_market_live_runs"}
        if not all(table_exists(con, table) for table in required):
            return {"status": "insufficient_data"}
        state_run = con.execute(
            "SELECT run_id FROM whole_market_state_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        state_row = con.execute(
            """SELECT trade_date,market_state_label,return_20,drawdown,realized_vol20,
            breadth_json,liquidity_json,regime_json FROM whole_market_state_daily
            WHERE run_id=? ORDER BY trade_date DESC LIMIT 1""",
            [state_run],
        ).fetchone()
        keys = (
            "trade_date",
            "state",
            "return_20",
            "drawdown",
            "realized_vol20",
            "breadth",
            "liquidity",
            "regime",
        )
        state = dict(zip(keys, state_row, strict=True))
        for key in ("breadth", "liquidity", "regime"):
            state[key] = json.loads(state[key] or "{}")
        live_run = con.execute(
            "SELECT run_id FROM whole_market_live_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        market = con.execute(
            """SELECT horizon,qualitative_state,median_return,downside_range,upside_range,
            regime,status FROM live_market_forecasts WHERE run_id=? ORDER BY horizon""",
            [live_run],
        ).df()
        sectors = con.execute(
            """SELECT sector,horizon,predicted_rank,score,status FROM live_sector_rank_forecasts
            WHERE run_id=? ORDER BY horizon,predicted_rank NULLS LAST""",
            [live_run],
        ).df()
        stocks = con.execute(
            """SELECT secid,horizon,predicted_rank,qualitative_state,predicted_return,status
            FROM live_stock_rank_forecasts WHERE run_id=? ORDER BY horizon,predicted_rank""",
            [live_run],
        ).df()
        evidence = con.execute(
            """SELECT count(*) total,count(*) FILTER(WHERE status='pending') pending
            FROM (SELECT status FROM live_market_forecasts WHERE run_id=? UNION ALL
                  SELECT status FROM live_stock_rank_forecasts WHERE run_id=?)""",
            [live_run, live_run],
        ).fetchone()
        matured = con.execute("SELECT count(*) FROM whole_market_live_outcomes").fetchone()[0]
        tournament = None
        if table_exists(con, "whole_market_tournament_runs"):
            tournament = con.execute(
                """SELECT entries,status,details_json FROM whole_market_tournament_runs
                ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
        return {
            "status": "ready",
            "state": state,
            "market": market,
            "sectors": sectors,
            "stocks": stocks,
            "live": {"total": evidence[0], "pending": evidence[1], "matured": matured},
            "tournament": tournament,
        }


def _direction(value: str) -> str:
    return {
        "up": "↗ рост",
        "positive": "↗ положительно",
        "down": "↘ снижение",
        "negative": "↘ отрицательно",
        "neutral": "→ нейтрально",
        "unknown": "⚪ недостаточно данных",
    }.get(value, "⚪ нет вывода")


def render() -> None:
    st.header("Рынок и прогноз")
    payload = load_dashboard_payload()
    if payload["status"] != "ready":
        st.info("⚪ Недостаточно данных для сводного анализа рынка.")
        return
    state = payload["state"]
    st.subheader(f"Рынок на {state['trade_date']:%d.%m.%Y}")
    state_label = {
        "stress": "🔴 стресс",
        "trend_up": "🟢 восходящий тренд",
        "trend_down": "🟠 нисходящий тренд",
        "transition_or_range": "🟡 переход / боковик",
    }.get(state["state"], "⚪ не определён")
    cols = st.columns(4)
    cols[0].metric("Состояние", state_label)
    cols[1].metric("20 сессий", f"{state['return_20']:+.1%}" if state["return_20"] is not None else "—")
    cols[2].metric("Просадка", f"{state['drawdown']:.1%}" if state["drawdown"] is not None else "—")
    cols[3].metric(
        "Волатильность", f"{state['realized_vol20']:.1%}" if state["realized_vol20"] is not None else "—"
    )
    positive, caution = market_reasons(state)
    left, right = st.columns(2)
    with left:
        st.markdown("**Что поддерживает рынок**")
        for reason in positive or ["Подтверждённых положительных факторов нет"]:
            st.write(f"• {reason}")
    with right:
        st.markdown("**Что ограничивает вывод**")
        for reason in caution or ["Явных предупреждений в текущем срезе нет"]:
            st.write(f"• {reason}")
    st.subheader("Горизонты рынка")
    market = payload["market"].copy()
    market["Вывод"] = market.qualitative_state.map(_direction)
    market["Диапазон 10–90%"] = market.apply(
        lambda row: (
            f"{row.downside_range:+.1%} … {row.upside_range:+.1%}"
            if pd.notna(row.downside_range) and pd.notna(row.upside_range)
            else "недостаточно данных"
        ),
        axis=1,
    )
    st.dataframe(
        market[["horizon", "Вывод", "Диапазон 10–90%", "regime", "status"]].rename(
            columns={"horizon": "Сессий", "regime": "Режим", "status": "Статус"}
        ),
        hide_index=True,
        use_container_width=True,
    )
    st.caption("Числовая вероятность не публикуется: probability gate остаётся закрытым.")
    st.subheader("Мои девять акций")
    stocks = payload["stocks"]
    selected_horizon = st.selectbox("Горизонт карточек", sorted(stocks.horizon.unique()), index=1)
    sector_snapshot = payload["sectors"][payload["sectors"].horizon == selected_horizon].set_index("sector")
    for row in stocks[stocks.horizon == selected_horizon].itertuples():
        with st.container(border=True):
            a, b, c, d = st.columns(4)
            a.markdown(f"### {row.secid}")
            b.metric("Ранг", f"{row.predicted_rank} из 9")
            c.metric("Исследовательский вывод", _direction(row.qualitative_state))
            d.metric("Статус", row.status)
            sector_name = SECTOR_MAP.get(row.secid)
            sector_rank = None
            if sector_name in sector_snapshot.index:
                sector_rank = sector_snapshot.loc[sector_name, "predicted_rank"]
            flow = st.columns(7)
            flow[0].caption(f"Рынок\n\n{state_label}")
            flow[1].caption(
                f"Сектор\n\n#{int(sector_rank)}" if pd.notna(sector_rank) else "Сектор\n\n⚪ нет данных"
            )
            flow[2].caption(f"Эмитент\n\n{_direction(row.qualitative_state)}")
            flow[3].caption(f"Ранг\n\n{row.predicted_rank}/9")
            flow[4].caption(
                "Analog\n\nесть оценка" if pd.notna(row.predicted_return) else "Analog\n\n⚪ нет оценки"
            )
            flow[5].caption("Риск\n\n🔴 высокий" if state["state"] == "stress" else "Риск\n\n🟡 обычный")
            flow[6].caption(f"Итог\n\n{_direction(row.qualitative_state)}")
            st.caption("Frozen fusion shadow; числовая вероятность скрыта действующей policy.")
    st.subheader("Отраслевой ранг")
    sector_horizon = min(selected_horizon, 120)
    st.dataframe(
        payload["sectors"][payload["sectors"].horizon == sector_horizon],
        hide_index=True,
        use_container_width=True,
    )
    live = payload["live"]
    st.subheader("Реальная проверка")
    st.write(f"Рынок и акции: всего {live['total']}; ожидают {live['pending']}; созрели {live['matured']}.")
    if live["matured"] < 50:
        st.info("⚪ Live-выборка пока слишком мала для вывода о качестве моделей.")


def render_advanced() -> None:
    render()
    st.caption("Research-only. Production Decision Engine и probability policy не изменены.")
