"""Stage 84 compact human dashboard backed by one saved evidence snapshot."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from moex_analytics.dashboard.data_access import read_connection, table_exists

HORIZONS = (5, 20, 60, 120, 250)
LABELS = {5: "Сейчас", 20: "1 месяц", 60: "3 месяца", 120: "6 месяцев", 250: "1 год"}


def evidence_badge(strength: str) -> str:
    return {"low": "● слабая", "medium": "●● средняя", "stronger": "●●● повышенная"}.get(strength, "● слабая")


def human_direction(value: str) -> str:
    return {"positive": "положительно", "neutral": "нейтрально", "negative": "отрицательно"}.get(
        value, "недостаточно данных"
    )


@st.cache_data(ttl=60, show_spinner=False)
def load_payload() -> dict[str, Any]:
    with read_connection() as con:
        required = {"portfolio_review_runs", "portfolio_verdict_runs", "portfolio_final_verdicts"}
        if not all(table_exists(con, table) for table in required):
            return {"status": "insufficient_data"}
        review = con.execute(
            """SELECT run_id,cutoff,verdict_run_id,consistency_hash FROM portfolio_review_runs
            WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        if table_exists(con, "investment_allocation_views"):
            verdicts = con.execute(
                """SELECT f.instrument,f.current_status,f.portfolio_action,f.risk_status,
                f.human_verdict,f.top_for_json,f.top_against_json,f.improve_json,f.worsen_json,
                v.investment_status,v.investment_reason,v.allocation_status,v.allocation_reason,
                v.portfolio_mode,v.current_weight,v.target_weight,v.max_weight
                FROM portfolio_final_verdicts f JOIN investment_allocation_views v
                USING(run_id,instrument) WHERE f.run_id=? ORDER BY f.instrument""",
                [review[2]],
            ).df()
        else:
            verdicts = con.execute(
                """SELECT instrument,current_status,portfolio_action,risk_status,human_verdict,
                top_for_json,top_against_json,improve_json,worsen_json,
                current_status investment_status,human_verdict investment_reason,
                portfolio_action allocation_status,human_verdict allocation_reason,
                'BUILDING' portfolio_mode,NULL current_weight,NULL target_weight,NULL max_weight
                FROM portfolio_final_verdicts WHERE run_id=? ORDER BY instrument""",
                [review[2]],
            ).df()
        horizons = con.execute(
            """SELECT instrument,horizon,directional_state,evidence_strength,relative_group,
            relative_rank,market_effect,sector_effect,strongest_evidence,analog_effect,news_effect,
            downside_state,portfolio_concentration,live_evidence,decision_eligible_blocks_json
            FROM portfolio_horizon_verdicts WHERE run_id=? ORDER BY instrument,horizon""",
            [review[2]],
        ).df()
        allocations = con.execute(
            """SELECT amount,allocation_json,cash_reserve,status,reason
            FROM portfolio_review_allocations WHERE run_id=? ORDER BY amount""",
            [review[0]],
        ).df()
        live = con.execute(
            """SELECT count(*) total,count(*) FILTER(WHERE status='matured') matured
            FROM (SELECT status FROM live_market_forecasts UNION ALL
                  SELECT status FROM live_stock_rank_forecasts)"""
        ).fetchone()
        state = con.execute(
            """SELECT market_state_label,return_20,drawdown,realized_vol20,volatility_json,
            rates_json FROM whole_market_state_daily ORDER BY trade_date DESC LIMIT 1"""
        ).fetchone()
        try:
            changes = con.execute(
                """SELECT secid,change_state,material,reasons_json FROM daily_decision_changes
                WHERE snapshot_id=(SELECT snapshot_id FROM daily_intelligence_snapshots
                ORDER BY created_at DESC LIMIT 1) ORDER BY material DESC,secid"""
            ).fetchall()
        except Exception:
            changes = []
        try:
            decision_outcomes = con.execute(
                """SELECT source_type,decision_type,horizon,observations,median_return,
                median_drawdown,objective_metric,sample_status FROM decision_outcome_scorecards
                ORDER BY source_type,decision_type,horizon"""
            ).df()
        except Exception:
            decision_outcomes = pd.DataFrame()
        return {
            "status": "ready",
            "review_id": review[0],
            "cutoff": review[1],
            "consistency_hash": review[3],
            "verdicts": verdicts,
            "horizons": horizons,
            "allocations": allocations,
            "live": {"total": live[0], "matured": live[1]},
            "market": {
                "state": state[0],
                "return_20": state[1],
                "drawdown": state[2],
                "volatility": state[3],
                "volatility_json": json.loads(state[4] or "{}"),
                "rates": json.loads(state[5] or "{}"),
            },
            "changes": [
                {
                    "secid": row[0],
                    "state": row[1],
                    "material": row[2],
                    "reasons": json.loads(row[3] or "[]"),
                }
                for row in changes
            ],
            "decision_outcomes": decision_outcomes,
        }


def _table(payload: dict[str, Any]) -> pd.DataFrame:
    horizons = payload["horizons"]
    verdicts = payload["verdicts"].set_index("instrument")
    rows = []
    for instrument, group in horizons.groupby("instrument"):
        values = group.set_index("horizon")
        row = {"Акция": instrument}
        for horizon in HORIZONS:
            item = values.loc[horizon]
            row[LABELS[horizon]] = (
                f"{human_direction(item.directional_state)} · {evidence_badge(item.evidence_strength)}"
            )
        row["Риск"] = verdicts.loc[instrument].risk_status
        verdict = verdicts.loc[instrument]
        row["Рыночный вывод"] = verdict.get("investment_status", verdict.get("portfolio_action"))
        row["Портфельный вывод"] = verdict.get("allocation_status", verdict.get("portfolio_action"))
        rows.append(row)
    return pd.DataFrame(rows)


def _overview(payload: dict[str, Any]) -> None:
    verdicts = payload["verdicts"]
    red = verdicts[verdicts.allocation_status.str.startswith("🔴")].instrument.tolist()
    ranking = payload["horizons"][payload["horizons"].horizon == 120].sort_values("relative_rank")
    strongest = ", ".join(ranking.head(3).instrument)
    weakest = ", ".join(ranking.tail(3).instrument)
    allocation = payload["allocations"][payload["allocations"].amount == 100_000].iloc[0]
    cols = st.columns(6)
    cols[0].metric("Портфель", "🟠 осторожность" if red else "🟡 наблюдать")
    cols[1].metric("Сильнее остальных", strongest)
    cols[2].metric("Слабее остальных", weakest)
    cols[3].metric("Главный риск", f"Концентрация: {', '.join(red)}" if red else "Режим рынка")
    cols[4].metric("Новые 100 тыс.", f"Резерв {allocation.cash_reserve:,.0f} ₽")
    cols[5].metric("Live", f"{payload['live']['matured']} проверено")


def _market(payload: dict[str, Any]) -> None:
    market = payload["market"]
    volatility = market["volatility_json"]
    rates = market["rates"]
    st.info(
        f"Рынок: **{market['state']}**. Просадка {market['drawdown']:.1%}; "
        f"волатильность {market['volatility']:.1%}; RVI {volatility.get('rvi', '—')}; "
        f"ключевая ставка {rates.get('cbr_key_rate', '—')}%. "
        f"Рост за 20 сессий {market['return_20']:+.1%} — контрфактор, но не отменяет stress."
    )


def render_today() -> None:
    st.header("Сегодня — единый вывод")
    payload = load_payload()
    if payload["status"] != "ready":
        st.info("⚪ Интегрированный evidence snapshot ещё не рассчитан.")
        return
    _overview(payload)
    _market(payload)
    st.subheader("Что изменилось после прошлого обновления")
    icons = {"IMPROVED": "🟢", "DETERIORATED": "🟠", "MIXED": "🟡", "UNCHANGED": "→"}
    material = [row for row in payload.get("changes", []) if row["material"]]
    if material:
        for row in material:
            st.write(f"{icons.get(row['state'], '→')} **{row['secid']}** — " + "; ".join(row["reasons"]))
    else:
        st.caption("→ Материальных изменений относительно прошлого торгового snapshot нет.")
    with st.expander("Как работали похожие решения в прошлом"):
        outcomes = payload.get("decision_outcomes", pd.DataFrame())
        if outcomes.empty:
            st.caption("Созревших outcomes пока нет. Live и research replay не смешиваются.")
        else:
            st.dataframe(outcomes, hide_index=True, use_container_width=True)
            st.caption(
                "historical_rule_replay — исследование правил; live_daily_snapshot — только реально "
                "сохранённые будущие verdicts. HOLD не оценивается как directional call."
            )
    st.dataframe(_table(payload), hide_index=True, use_container_width=True)
    st.caption(f"Единый snapshot: {payload['cutoff']} · {payload['consistency_hash'][:12]}")
    st.caption("Research evidence не является числовой вероятностью или торговой рекомендацией.")


def render_stocks() -> None:
    st.header("Мои акции — почему такой вывод")
    payload = load_payload()
    if payload["status"] != "ready":
        st.info("⚪ Недостаточно данных.")
        return
    verdicts = payload["verdicts"].set_index("instrument")
    for instrument, group in payload["horizons"].groupby("instrument"):
        verdict = verdicts.loc[instrument]
        with st.expander(f"{instrument} — {verdict.investment_status}"):
            market_column, portfolio_column = st.columns(2)
            market_column.markdown("**РЫНОЧНЫЙ ВЫВОД**")
            market_column.write(verdict.investment_status)
            market_column.caption(verdict.investment_reason)
            portfolio_column.markdown("**ПОРТФЕЛЬНЫЙ ВЫВОД**")
            portfolio_column.write(verdict.allocation_status)
            portfolio_column.caption(verdict.allocation_reason)
            st.dataframe(
                group.assign(
                    Горизонт=group.horizon.map(LABELS),
                    Вывод=group.directional_state.map(human_direction),
                    Доказательность=group.evidence_strength.map(evidence_badge),
                )[["Горизонт", "Вывод", "Доказательность", "relative_group"]],
                hide_index=True,
                use_container_width=True,
            )
            left, right = st.columns(2)
            with left:
                st.markdown("**В пользу**")
                for reason in json.loads(verdict.top_for_json or "[]") or [
                    "Устойчивого направленного edge нет"
                ]:
                    st.write(f"• {reason}")
            with right:
                st.markdown("**Против**")
                for reason in json.loads(verdict.top_against_json or "[]"):
                    st.write(f"• {reason}")
            st.markdown("**Что изменит вывод**")
            st.write("• " + "\n• ".join(json.loads(verdict.improve_json)))
            with st.popover("На чём основан вывод"):
                st.write("✓ рынок · ✓ сектор · ✓ ставки · ✓ относительный рейтинг")
                st.write("✓ исторические аналоги · ✓ риск · ✓ портфель · ✓ live")
                st.write("◐ новости и фундаментал: частичное покрытие")
            with st.expander("Advanced evidence"):
                st.dataframe(
                    group[
                        [
                            "horizon",
                            "strongest_evidence",
                            "analog_effect",
                            "downside_state",
                            "live_evidence",
                            "decision_eligible_blocks_json",
                        ]
                    ],
                    hide_index=True,
                    use_container_width=True,
                )


def render_scenarios() -> None:
    st.header("Сценарии рынка и моего портфеля")
    try:
        with read_connection() as con:
            run = con.execute(
                "SELECT run_id,cutoff,episodes FROM portfolio_scenario_runs "
                "WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            root = con.execute(
                "SELECT market_regime,news_overlay_json FROM portfolio_scenario_roots WHERE run_id=?",
                [run[0]],
            ).fetchone()
            branches = con.execute(
                """SELECT branch_id,label,episodes,total_episodes,median_imoex_return,
                median_drawdown,representative_date,historical_frequency_text
                FROM portfolio_scenario_branches WHERE run_id=? ORDER BY episodes DESC""",
                [run[0]],
            ).fetchall()
    except Exception:
        st.info("⚪ Дерево реальных исторических сценариев ещё не рассчитано.")
        return
    st.caption(f"СЕГОДНЯ · {run[1]} · режим рынка: {root[0]}. Ветви — реальные эпизоды, не вероятности.")
    columns = st.columns(len(branches))
    for column, branch in zip(columns, branches, strict=True):
        with column:
            st.markdown(f"**{branch[1]}**")
            st.write(branch[7])
            st.write(f"Медиана IMOEX: {branch[4]:+.1%}")
            st.write(f"Просадка: {branch[5]:.1%}")
            st.caption(f"Реальный представитель: {branch[6]}")
    selected_label = st.selectbox("Открыть ветвь", [branch[1] for branch in branches])
    selected = next(branch for branch in branches if branch[1] == selected_label)
    with read_connection() as con:
        paths = con.execute(
            """SELECT analog_date,relative_session,normalized_imoex
            FROM portfolio_scenario_paths WHERE run_id=? AND branch_id=?
            ORDER BY analog_date,relative_session""",
            [run[0], selected[0]],
        ).df()
        sensitivities = con.execute(
            """SELECT secid,episodes,median_return,median_relative_return,
            median_drawdown,resilience FROM portfolio_scenario_sensitivities
            WHERE run_id=? AND branch_id=? ORDER BY median_relative_return DESC""",
            [run[0], selected[0]],
        ).df()
    figure = go.Figure()
    for analog_date, group in paths.groupby("analog_date"):
        figure.add_trace(
            go.Scatter(
                x=group.relative_session,
                y=group.normalized_imoex,
                mode="lines",
                name=str(analog_date),
                opacity=0.45,
            )
        )
    figure.update_layout(
        height=420,
        template="plotly_white",
        xaxis_title="Торговые сессии после исторического T0",
        yaxis_title="IMOEX, T0 = 100",
    )
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})
    st.subheader("Как мои бумаги вели себя в этой ветви")
    st.dataframe(sensitivities, hide_index=True, use_container_width=True)
    stories = json.loads(root[1] or "[]")
    if stories:
        st.caption("Текущие новости показаны только как контекст и не меняют веса ветвей.")
        for story in stories:
            st.write(f"• {story['headline']}")


def render_allocation() -> None:
    st.header("Куда вложить пополнение")
    payload = load_payload()
    if payload["status"] != "ready":
        st.info("⚪ Недостаточно данных.")
        return
    amount = st.selectbox("Сумма", payload["allocations"].amount.astype(int).tolist(), index=1)
    row = payload["allocations"][payload["allocations"].amount == amount].iloc[0]
    st.subheader("Лучшие бумаги по рыночной привлекательности")
    market = payload["verdicts"].copy()
    market["priority"] = market.investment_status.map(
        lambda value: 0 if value.startswith("🟢") else 1 if value.startswith("🟡") else 2
    )
    st.dataframe(
        market.sort_values(["priority", "instrument"])[["instrument", "investment_status"]],
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("Как добавить их именно в мой портфель")
    st.warning(f"{row.status}: оставить в резерве {row.cash_reserve:,.0f} ₽")
    st.write(row.reason)
    st.json(json.loads(row.allocation_json))
    st.caption("Покупки не форсируются: направленное преимущество ещё не доказано live-проверкой.")


def answer_question(payload: dict[str, Any], question: str) -> str:
    verdicts = payload["verdicts"].set_index("instrument")
    horizons = payload["horizons"]
    if "100" in question or "резерв" in question.lower():
        row = payload["allocations"][payload["allocations"].amount == 100_000].iloc[0]
        return f"Оставить {row.cash_reserve:,.0f} ₽ в резерве: {row.reason} Cutoff {payload['cutoff']}."
    if "МТС" in question.upper() or "MTSS" in question.upper():
        row = horizons[(horizons.instrument == "MTSS") & (horizons.horizon == 120)].iloc[0]
        return (
            f"MTSS на 6 месяцев: {human_direction(row.directional_state)}, "
            f"{evidence_badge(row.evidence_strength)}. Модель исторически снижала ошибку оценки, "
            f"но вероятность роста не доказана. {verdicts.loc['MTSS'].portfolio_action}."
        )
    if "СБЕР" in question.upper() or "SBER" in question.upper():
        sber = verdicts.loc["SBERP"]
        return (
            f"По самой бумаге: {sber.get('investment_status', sber.portfolio_action)} — "
            f"{sber.get('investment_reason', 'направленный edge не доказан')}. "
            f"По вашему портфелю: {sber.get('allocation_status', sber.portfolio_action)} — "
            f"{sber.get('allocation_reason', 'ограничение не рассчитано')}. "
            "Исторические сценарии являются диапазоном реальных эпизодов, не вероятностью."
        )
    if "6 месяцев" in question:
        best = horizons[horizons.horizon == 120].sort_values("relative_rank").iloc[0]
        return (
            f"На 6 месяцев выше остальных расположен {best.instrument}; это относительный "
            f"research rank, не вероятность роста. Cutoff {payload['cutoff']}."
        )
    if "риск" in question.lower():
        red = verdicts[verdicts.portfolio_action.str.startswith("🔴")].index.tolist()
        return f"Основной риск: stress рынка и концентрация {', '.join(red) or 'не выявлена'}."
    best = horizons[horizons.horizon == 120].sort_values("relative_rank").head(3).instrument.tolist()
    return (
        f"Относительно сильнее сейчас: {', '.join(best)}. Все выводы research-only; "
        "направленный edge не доказан."
    )


def render_ask() -> None:
    st.header("Спросить про портфель")
    payload = load_payload()
    if payload["status"] != "ready":
        st.info("⚪ Недостаточно данных.")
        return
    questions = [
        "Что сейчас лучше выглядит?",
        "Что с МТС?",
        "Почему Сбер не зелёный?",
        "Почему оставить 100 тысяч в резерве?",
        "Какая бумага сильнее на 6 месяцев?",
        "Где основной риск?",
    ]
    question = st.selectbox("Вопрос", questions)
    st.success(answer_question(payload, question))
    st.caption(f"Ответ из snapshot {payload['consistency_hash'][:12]}; probability gate закрыт.")


def render_risks() -> None:
    st.header("Риски")
    payload = load_payload()
    if payload["status"] == "ready":
        _market(payload)
        st.dataframe(
            payload["verdicts"][["instrument", "risk_status", "portfolio_action"]],
            hide_index=True,
            use_container_width=True,
        )


def render_live() -> None:
    st.header("Реальная проверка")
    payload = load_payload()
    if payload["status"] != "ready":
        st.info("⚪ Недостаточно данных.")
        return
    matured, total = payload["live"]["matured"], payload["live"]["total"]
    st.metric("Проверено независимых исходов", matured)
    st.metric("Ожидается", total - matured)
    st.progress(min(matured / 50, 1.0), text=f"До первой контрольной точки: {matured} / 50")
    st.caption("50 — только первая контрольная точка, а не гарантия качества.")
