"""Stage 94 BASIC investor cockpit: actual history and clearly separated scenarios."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from moex_analytics.conditioned_stock_forecasting.core import SECIDS
from moex_analytics.dashboard.data_access import read_connection

HORIZON_LABELS = {
    1: "1 день",
    5: "1 неделя",
    20: "1 месяц",
    40: "2 месяца",
    60: "3 месяца",
    80: "4 месяца",
    100: "5 месяцев",
    120: "6 месяцев",
    250: "1 год",
}


def projection_figure(history: pd.DataFrame, bands: pd.DataFrame, paths: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=list(range(-len(history) + 1, 1)),
            y=history.close,
            name="Фактическая цена",
            line={"color": "#111827", "width": 3},
            customdata=history.trade_date.astype(str),
            hovertemplate="Дата %{customdata}<br>Цена %{y:.2f} ₽<extra></extra>",
        )
    )
    if bands.empty:
        return figure
    figure.add_trace(
        go.Scatter(
            x=bands.relative_session,
            y=bands.q90_price,
            name="10–90%",
            line={"color": "rgba(59,130,246,0)"},
            showlegend=False,
        )
    )
    labels = bands[bands.relative_session.isin(HORIZON_LABELS)]
    figure.add_trace(
        go.Scatter(
            x=labels.relative_session,
            y=labels.median_price,
            mode="markers+text",
            text=[f"{HORIZON_LABELS[int(x)]}<br>{y:.2f} ₽" for x, y in zip(
                labels.relative_session, labels.median_price, strict=True
            )],
            textposition="top center",
            name="Контрольные сроки",
            marker={"color": "#2563eb", "size": 7},
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=bands.relative_session,
            y=bands.q10_price,
            name="Широкий исторический диапазон 10–90%",
            fill="tonexty",
            fillcolor="rgba(59,130,246,0.12)",
            line={"color": "rgba(59,130,246,0)"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=bands.relative_session,
            y=bands.q75_price,
            name="25–75%",
            line={"color": "rgba(37,99,235,0)"},
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=bands.relative_session,
            y=bands.q25_price,
            name="Основной исторический диапазон 25–75%",
            fill="tonexty",
            fillcolor="rgba(37,99,235,0.22)",
            line={"color": "rgba(37,99,235,0)"},
        )
    )
    for analog_date, group in paths.groupby("analog_date"):
        figure.add_trace(
            go.Scatter(
                x=group.relative_session,
                y=group.projected_price,
                name=f"Реальный эпизод {analog_date}",
                line={"color": "rgba(107,114,128,0.25)", "width": 1},
                showlegend=False,
            )
        )
    figure.add_trace(
        go.Scatter(
            x=bands.relative_session,
            y=bands.median_price,
            name="Центральный исторический сценарий",
            line={"color": "#2563eb", "width": 4, "dash": "dash"},
        )
    )
    medoid = paths[paths.is_medoid]
    if not medoid.empty:
        figure.add_trace(
            go.Scatter(
                x=medoid.relative_session,
                y=medoid.projected_price,
                name=f"Представительный реальный эпизод {medoid.iloc[0].analog_date}",
                line={"color": "#f59e0b", "width": 2},
            )
        )
    figure.add_vline(x=0, line_width=2, line_dash="dot", line_color="#dc2626")
    figure.add_annotation(x=0, y=1, yref="paper", text="СЕГОДНЯ / T0", showarrow=False)
    figure.update_layout(
        height=620,
        template="plotly_white",
        xaxis_title="Торговые сессии относительно TODAY / T0",
        yaxis_title="Цена, ₽",
        legend={"orientation": "h", "y": -0.18},
    )
    return figure


def conditional_projection_figure(
    history: pd.DataFrame, curves: pd.DataFrame, paths: pd.DataFrame, show_raw: bool
) -> go.Figure:
    """Observed history plus explicitly unvalidated conditional research paths."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=list(range(-len(history) + 1, 1)), y=history.close,
            name="Фактическая цена", line={"color": "#111827", "width": 3},
            customdata=history.trade_date.astype(str),
            hovertemplate="Дата %{customdata}<br>Цена %{y:.2f} ₽<extra></extra>",
        )
    )
    if curves.empty or curves.weighted_median_price.isna().all():
        return figure
    figure.add_trace(
        go.Scatter(
            x=curves.session, y=curves.plausible80_high, line={"color": "rgba(59,130,246,0)"},
            name="Plausible 80% (не подтверждён)", showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=curves.session, y=curves.plausible80_low, fill="tonexty",
            fillcolor="rgba(59,130,246,0.12)", line={"color": "rgba(59,130,246,0)"},
            name="Plausible 80% (не подтверждён)",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=curves.session, y=curves.expected60_high, line={"color": "rgba(37,99,235,0)"},
            name="Expected 60% (не подтверждён)", showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=curves.session, y=curves.expected60_low, fill="tonexty",
            fillcolor="rgba(37,99,235,0.24)", line={"color": "rgba(37,99,235,0)"},
            name="Expected 60% (не подтверждён)",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=curves.session, y=curves.weighted_median_price,
            name="Conditional weighted median path",
            line={"color": "#2563eb", "width": 4, "dash": "dash"},
        )
    )
    if curves.stress_low.notna().any():
        figure.add_trace(
            go.Scatter(
                x=curves.session, y=curves.stress_low, name="Stress envelope",
                line={"color": "#dc2626", "width": 1, "dash": "dot"},
            )
        )
        figure.add_trace(
            go.Scatter(
                x=curves.session, y=curves.stress_high, name="Stress envelope upper",
                line={"color": "#dc2626", "width": 1, "dash": "dot"}, showlegend=False,
            )
        )
    if show_raw:
        for analog_date, group in paths.groupby("analog_date"):
            figure.add_trace(
                go.Scatter(
                    x=group.session, y=group.projected_price,
                    name=f"Observed branch {analog_date}",
                    line={"color": "rgba(107,114,128,0.35)", "width": 1}, showlegend=False,
                )
            )
    figure.add_vline(x=0, line_width=2, line_dash="dot", line_color="#dc2626")
    figure.add_annotation(x=0, y=1, yref="paper", text="СЕГОДНЯ / T0", showarrow=False)
    figure.update_layout(
        height=620, template="plotly_white",
        xaxis_title="Торговые сессии относительно TODAY / T0", yaxis_title="Цена, ₽",
        legend={"orientation": "h", "y": -0.18},
    )
    return figure


def _load(secid: str) -> dict[str, Any]:
    with read_connection() as con:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }
        run = con.execute(
            "SELECT run_id,cutoff FROM price_scenario_runs WHERE status='completed' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        projection_run = con.execute(
            "SELECT projection_run_id FROM price_scenario_runs WHERE run_id=?", [run[0]]
        ).fetchone()[0]
        view = con.execute(
            """SELECT investment_status,investment_reason,allocation_status,allocation_reason,
            portfolio_mode,current_weight,target_weight,max_weight FROM investment_allocation_views
            WHERE run_id=(SELECT run_id FROM portfolio_verdict_runs WHERE status='completed'
            ORDER BY created_at DESC LIMIT 1) AND instrument=?""",
            [secid],
        ).fetchone()
        history = con.execute(
            """SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=?
            AND trade_date<=? ORDER BY trade_date DESC LIMIT 120""",
            [secid, run[1]],
        ).df().sort_values("trade_date")
        bands = con.execute(
            """SELECT relative_session,q10_price,q25_price,median_price,q75_price,q90_price,
            analog_count FROM analog_projection_bands WHERE run_id=? AND secid=?
            ORDER BY relative_session""",
            [projection_run, secid],
        ).df()
        paths = con.execute(
            """SELECT analog_date,relative_session,projected_price,is_medoid
            FROM analog_projected_paths WHERE run_id=? AND secid=? ORDER BY analog_date,relative_session""",
            [projection_run, secid],
        ).df()
        horizons = con.execute(
            """SELECT horizon,status,current_price,central_price,median_return,q10_price,q25_price,
            q75_price,q90_price,analog_count,median_max_drawdown,above_count,medoid_analog_date
            FROM analog_projection_horizons WHERE run_id=? AND secid=? ORDER BY horizon""",
            [projection_run, secid],
        ).df()
        branches = con.execute(
            """SELECT branch,label,episodes,medoid_analog_date,terminal_prices_json,max_drawdown,
            status FROM price_scenario_branches WHERE run_id=? AND secid=? ORDER BY branch""",
            [run[0], secid],
        ).df()
        touches = con.execute(
            """SELECT horizon,analog_count,touch_down_5,touch_down_10,touch_up_5,touch_up_10
            FROM price_scenario_touch_memory WHERE run_id=? AND secid=? ORDER BY horizon""",
            [run[0], secid],
        ).df()
        conditional = None
        if "conditional_validation_runs" in tables:
            validation = con.execute(
                """SELECT run_id,cutoff FROM conditional_validation_runs
                WHERE status='completed' ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
            if validation:
                history = con.execute(
                    """SELECT trade_date,close FROM canonical_daily_prices
                    WHERE canonical_secid=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 120""",
                    [secid, validation[1]],
                ).df().sort_values("trade_date")
                path_run = con.execute(
                    """SELECT run_id FROM conditional_path_runs WHERE status='completed'
                    ORDER BY created_at DESC LIMIT 1"""
                ).fetchone()[0]
                regime_run = con.execute(
                    """SELECT run_id FROM conditional_regime_runs WHERE status='completed'
                    ORDER BY created_at DESC LIMIT 1"""
                ).fetchone()[0]
                current = con.execute(
                    """SELECT horizon,current_price,center_price,expected60_low,expected60_high,
                    plausible80_low,plausible80_high,stress_low,stress_high,up_probability,
                    probability_published,raw_n,effective_sample_size,reliability,range_status
                    FROM conditional_calibrated_forecasts WHERE run_id=? AND secid=?
                    ORDER BY horizon""",
                    [validation[0], secid],
                ).df()
                curves = con.execute(
                    """SELECT session,weighted_median_price,expected_low expected60_low,
                    expected_high expected60_high,plausible_low plausible80_low,
                    plausible_high plausible80_high,stress_low,stress_high
                    FROM conditional_path_curves WHERE run_id=? AND secid=? ORDER BY session""",
                    [path_run, secid],
                ).df()
                conditional_paths = con.execute(
                    """SELECT analog_date,session,projected_price FROM conditional_analog_paths
                    WHERE run_id=? AND secid=? AND scenario_role='EXPECTED_CONDITIONAL'
                    ORDER BY analog_date,session""",
                    [path_run, secid],
                ).df()
                risk = con.execute(
                    """SELECT horizon,raw_n,effective_sample_size,dd5_frequency,dd10_frequency,
                    median_max_drawdown,evidence_status FROM conditional_path_risk
                    WHERE run_id=? AND secid=? ORDER BY horizon""",
                    [path_run, secid],
                ).df()
                barrier = con.execute(
                    """SELECT horizon,upper_barrier,lower_barrier,raw_n,effective_sample_size,
                    upper_first_count,lower_first_count,neither_count,median_time_upper,
                    median_time_lower,evidence_status,probability_published
                    FROM conditional_barrier_results WHERE run_id=(SELECT run_id
                    FROM conditional_barrier_runs WHERE status='completed'
                    ORDER BY created_at DESC LIMIT 1) AND secid=? AND is_symmetric
                    AND upper_barrier IN (0.05,0.10) ORDER BY horizon,upper_barrier""",
                    [secid],
                ).df()
                regime = con.execute(
                    """SELECT market_trend,volatility_regime,rates_regime,stock_state
                    FROM conditional_regime_timeline WHERE run_id=? AND secid=?
                    ORDER BY trade_date DESC LIMIT 1""",
                    [regime_run, secid],
                ).fetchone()
                roles = con.execute(
                    """SELECT scenario_role,count(*) episodes FROM regime_conditioned_analogs
                    WHERE run_id=? AND secid=? GROUP BY scenario_role ORDER BY scenario_role""",
                    [regime_run, secid],
                ).df()
                validation_rows = con.execute(
                    """SELECT horizon,oos_n,mae,directional_accuracy,brier,coverage60,
                    median_width60,coverage80,median_width80,median_ess,selected_threshold,
                    reliability,reliability_reason FROM conditional_validation_scorecards
                    WHERE run_id=? AND secid=? AND variant='plus_weighting' ORDER BY horizon""",
                    [validation[0], secid],
                ).df()
                conditional = {
                    "run_id": validation[0], "cutoff": validation[1], "current": current,
                    "curves": curves, "paths": conditional_paths, "risk": risk,
                    "barrier": barrier, "regime": regime, "roles": roles,
                    "validation": validation_rows,
                }
    return {"run_id": run[0], "cutoff": run[1], "view": view, "history": history,
            "bands": bands, "paths": paths, "horizons": horizons, "branches": branches,
            "touches": touches, "conditional": conditional}


def render() -> None:
    st.header("Акции — инвестиционный вывод и исторические сценарии")
    secid = st.selectbox("Бумага", SECIDS, index=SECIDS.index("SBERP"))
    try:
        payload = _load(secid)
    except Exception:
        st.info("⚪ Сценарная проекция ещё не рассчитана.")
        return
    view = payload["view"]
    if view:
        left, right = st.columns(2)
        left.markdown("### РЫНОЧНАЯ ОЦЕНКА")
        left.markdown(f"**{view[0]}**")
        left.caption(view[1])
        right.markdown("### ПОРТФЕЛЬ")
        right.markdown(f"**{view[2]}**")
        right.caption(view[3])
        right.caption(
            f"Режим: {view[4]} · текущий вес: {view[5]:.1%} · "
            f"желаемый: {view[6] if view[6] is not None else 'не задан'}"
        )
    conditional = payload["conditional"]
    if conditional is not None:
        current = conditional["current"]
        if current.empty or current.center_price.isna().all():
            st.warning("⚪ Недостаточно conditional history для этой бумаги.")
            st.caption("Synthetic history не создаётся. Stress history остаётся в audit layer.")
            return
        regime = conditional["regime"]
        headline = current[current.horizon == 60]
        headline = headline.iloc[0] if not headline.empty else current.iloc[0]
        first, second, third, fourth = st.columns(4)
        first.metric("Текущая цена", f"{headline.current_price:.2f} ₽")
        second.metric("Forecast reliability", headline.reliability)
        third.metric(
            "Conditional analogs", f"N={headline.raw_n:g} · ESS={headline.effective_sample_size:.2f}"
        )
        if regime:
            fourth.metric("Текущий режим", f"{regime[0]} / {regime[1]}")
            st.caption(
                f"Рынок: {regime[0]} · волатильность: {regime[1]} · "
                f"ставки: {regime[2]} · бумага: {regime[3]}"
            )
        roles = conditional["roles"]
        if not roles.empty:
            st.caption(
                "Режимные эпизоды: "
                + " · ".join(f"{row.scenario_role}: {row.episodes:g}" for row in roles.itertuples())
            )
        st.warning(
            "Conditional ranges — исследовательские и не подтверждены, пока reliability не VALIDATED. "
            "Stress envelope показан отдельно. Числовая вероятность роста закрыта probability gate."
        )
        show_raw = st.toggle("Показать реальные historical branches", value=False)
        st.plotly_chart(
            conditional_projection_figure(
                payload["history"], conditional["curves"], conditional["paths"], show_raw
            ),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        relevant = current[current.horizon.isin((5, 20, 60, 120, 250))].copy()
        risk = conditional["risk"]
        relevant = relevant.merge(risk, on="horizon", how="left", suffixes=("", "_risk"))
        relevant["Срок"] = relevant.horizon.map(HORIZON_LABELS)
        relevant["Центр"] = relevant.center_price.map(lambda value: f"{value:.2f} ₽")
        relevant["Expected 60%"] = relevant.apply(
            lambda row: f"{row.expected60_low:.2f}–{row.expected60_high:.2f} ₽\nне подтверждено",
            axis=1,
        )
        relevant["Plausible 80%"] = relevant.apply(
            lambda row: f"{row.plausible80_low:.2f}–{row.plausible80_high:.2f} ₽\nне подтверждено",
            axis=1,
        )
        relevant["P(up)"] = "скрыта — probability gate"
        relevant["DD ≥10%"] = relevant.apply(
            lambda row: (
                f"LOW EVIDENCE · N={row.raw_n:g}, ESS={row.effective_sample_size:.2f}"
                if row.evidence_status == "insufficient_evidence"
                else f"{row.dd10_frequency:.1%} · uncalibrated"
            ),
            axis=1,
        )
        relevant["Надёжность"] = relevant.reliability
        st.dataframe(
            relevant[[
                "Срок", "Центр", "Expected 60%", "Plausible 80%", "P(up)",
                "DD ≥10%", "Надёжность",
            ]],
            hide_index=True,
            use_container_width=True,
        )
        st.subheader("Что произойдёт раньше — цель или снижение")
        barrier = conditional["barrier"]
        barrier = barrier[
            barrier.horizon.isin((20, 60, 120, 250)) & barrier.upper_barrier.isin((0.05, 0.10))
        ]
        for row in barrier.itertuples():
            label = (
                f"+{row.upper_barrier:.0%} раньше −{row.lower_barrier:.0%} · "
                f"{HORIZON_LABELS[row.horizon]}"
            )
            if row.probability_published:
                text = "validated probability available"
            else:
                text = (
                    f"LOW EVIDENCE: верх первым {row.upper_first_count:g}, "
                    f"низ первым {row.lower_first_count:g}, не достигнуто {row.neither_count:g}; "
                    f"N={row.raw_n:g}, ESS={row.effective_sample_size:.2f}"
                )
            st.write(f"**{label}:** {text}")
        with st.expander("Validation и calibration"):
            st.dataframe(conditional["validation"], hide_index=True, use_container_width=True)
        with st.expander("Исторические stress branches"):
            st.dataframe(payload["branches"], hide_index=True, use_container_width=True)
        st.info(
            "Ни одна displayed research frequency не является обещанием результата. "
            "Production Decision Engine и Portfolio Allocation View не изменены."
        )
        st.caption(
            f"Cutoff {conditional['cutoff']} · run {conditional['run_id']} · "
            "frozen OOS · probability gate unchanged"
        )
        return
    if payload["bands"].empty:
        st.warning("⚪ Недостаточно независимой истории для сценарной проекции этой бумаги.")
        return
    st.plotly_chart(
        projection_figure(payload["history"], payload["bands"], payload["paths"]),
        use_container_width=True,
        config={"displayModeBar": False},
    )
    st.warning(
        "Правая часть графика — не известное будущее. Она показывает, как развивались "
        "реальные похожие ситуации после точки сравнения."
    )
    table = payload["horizons"].copy()
    table["Срок"] = table.horizon.map(HORIZON_LABELS)
    table["Центральный сценарий"] = table.central_price
    table["Основной диапазон"] = table.apply(
        lambda row: f"{row.q25_price:.2f}–{row.q75_price:.2f} ₽" if row.status == "ready" else "—",
        axis=1,
    )
    table["Широкий диапазон"] = table.apply(
        lambda row: f"{row.q10_price:.2f}–{row.q90_price:.2f} ₽" if row.status == "ready" else "—",
        axis=1,
    )
    table["Аналогов"] = table.analog_count
    table["Типичная просадка"] = table.median_max_drawdown
    st.dataframe(
        table[["Срок", "Центральный сценарий", "Основной диапазон", "Широкий диапазон",
               "Аналогов", "Типичная просадка"]],
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("Реальные исторические ветки")
    columns = st.columns(4)
    for column, row in zip(columns, payload["branches"].itertuples(), strict=True):
        with column:
            st.markdown(f"**{row.label}**")
            st.write(f"Эпизодов: {row.episodes}")
            st.write(f"Представитель: {row.medoid_analog_date or '—'}")
            st.write(f"Просадка пути: {row.max_drawdown:.1%}" if pd.notna(row.max_drawdown) else "—")
            prices = json.loads(row.terminal_prices_json or "{}")
            if prices:
                st.caption(
                    " · ".join(
                        f"{HORIZON_LABELS[int(key)]}: {value:.2f} ₽"
                        for key, value in prices.items()
                    )
                )
    st.subheader("Что это означает")
    st.info(
        "Исторические аналоги задают сценарный диапазон, но направленное OOS-преимущество "
        "не доказано. Это не обещание цены и не числовая вероятность."
    )
    if not payload["touches"].empty:
        row = payload["touches"].iloc[-1]
        st.caption(
            f"До горизонта 1 год: в {row.touch_down_5:g} из {row.analog_count:g} реальных "
            f"эпизодов путь касался −5%; в {row.touch_up_5:g} из {row.analog_count:g} — +5%."
        )
    st.caption(f"Cutoff {payload['cutoff']} · research-only · probability gate unchanged")
