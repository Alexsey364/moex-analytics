"""Russian, three-layer portfolio dashboard backed by deterministic daily reports."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from moex_analytics.database import connection
from moex_analytics.portfolio_research.human_intelligence import INTENTS, answer_question
from moex_analytics.portfolio_research.portfolio_editor import (
    instrument_registry,
    load_positions,
    position_diff,
    recalculate_portfolio,
    save_positions,
    validate_positions,
)
from moex_analytics.portfolio_research.visual_assistant import (
    STATUS,
    confidence_dots,
    horizon_label,
    plan_allocation,
    status_change,
    status_label,
    visual_status,
)
from moex_analytics.transparency import explain_current_decision

st.markdown(
    """<style>
    .status-card{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:.75rem 1rem;
      margin:.35rem 0;background:rgba(128,128,128,.04)}
    .status-pill{display:inline-block;border-radius:999px;padding:.2rem .65rem;font-weight:600;
      border:1px solid rgba(128,128,128,.3)}
    </style>""",
    unsafe_allow_html=True,
)


def _q(sql, params=None):
    with connection(read_only=True) as con:
        return con.execute(sql, params or []).df()


def _latest_report():
    frame = _q("SELECT * FROM human_daily_reports ORDER BY created_at DESC LIMIT 1")
    return None if frame.empty else frame.iloc[0]


def _synthesis():
    report = _latest_report()
    if report is None:
        return report, pd.DataFrame()
    frame = _q(
        "SELECT * FROM human_instrument_synthesis WHERE report_id=? ORDER BY equity_weight DESC",
        [report.report_id],
    )
    return report, _decorate(frame, report)


def _decorate(frame, report):
    if frame.empty:
        return frame
    risks = _q(
        "SELECT replace(factor,'risk_contribution:','') secid,exposure FROM portfolio_factor_exposures "
        "WHERE snapshot_id=? AND factor LIKE 'risk_contribution:%'",
        [report.portfolio_snapshot_id],
    )
    risk_map = dict(zip(risks.secid, risks.exposure, strict=False))
    blocks = _q(
        "SELECT secid,block_id,status FROM human_intelligence_blocks WHERE report_id=? "
        "AND block_id IN ('business_quality','research_signal')",
        [report.report_id],
    )
    block_map = {(row.secid, row.block_id): row.status for row in blocks.itertuples()}
    previous = _q(
        "SELECT report_id FROM human_daily_reports WHERE created_at < ? ORDER BY created_at DESC LIMIT 1",
        [report.created_at],
    )
    previous_status = {}
    if not previous.empty:
        old = _q(
            "SELECT secid,action_group,confidence_score,data_status,equity_weight "
            "FROM human_instrument_synthesis WHERE report_id=?",
            [previous.iloc[0].report_id],
        )
        for row in old.itertuples():
            previous_status[row.secid] = visual_status(
                action_group=row.action_group,
                confidence=row.confidence_score,
                data_status=row.data_status,
                weight=row.equity_weight,
            )
    statuses, changes = [], []
    for row in frame.itertuples():
        current = visual_status(
            action_group=row.action_group,
            confidence=row.confidence_score,
            data_status=row.data_status,
            fundamental_status=block_map.get((row.secid, "business_quality"), "unknown"),
            research_status=block_map.get((row.secid, "research_signal"), "unknown"),
            weight=row.equity_weight,
            risk_contribution=float(risk_map.get(row.secid, 0)),
        )
        statuses.append(current)
        changes.append(status_change(current, previous_status.get(row.secid)))
    frame = frame.copy()
    frame["visual_status"] = statuses
    frame["status_change"] = changes
    frame["risk_contribution"] = frame.secid.map(risk_map).fillna(0)
    frame["status_rank"] = frame.visual_status.map(lambda value: STATUS[value][2])
    return frame.sort_values(["status_rank", "confidence_score"], ascending=[True, False])


def _empty():
    st.info("Недостаточно данных. Выполните ежедневный анализ на странице «Обновить данные».")


def _money(value):
    return "—" if pd.isna(value) else f"{value:,.2f} ₽".replace(",", " ")


def _pct(value):
    return "—" if pd.isna(value) else f"{value:.2%}"


def _loads(value):
    try:
        return json.loads(value) if isinstance(value, str) else value or []
    except (TypeError, ValueError):
        return []


def _human_table(frame):
    columns = {
            "Статус": frame.visual_status.map(status_label),
            "Акция": frame.secid,
            "Цена": frame.current_price.map(_money),
            "Мой вес": frame.equity_weight.map(_pct),
            "1–5 дней": frame.short_term_view.map(horizon_label),
            "1 месяц": frame.medium_term_view.map(horizon_label),
            "3–12 месяцев": frame.long_term_view.map(horizon_label),
            "Дивиденд": frame.dividend_view,
            "Риск": frame.risk_view,
            "Уверенность": frame.confidence_label.map(confidence_dots),
            "Изменилось": frame.status_change,
            "Действие": frame.visual_status.map(lambda value: STATUS[value][1]),
        }
    if "investment_view" in frame:
        columns["Инвестиционная оценка"] = frame.investment_view
        columns["Портфельное ограничение"] = frame.allocation_view
    return pd.DataFrame(columns)


def _add_decision_views(frame):
    result = frame.copy()
    with connection(read_only=False) as con:
        traces = {secid: explain_current_decision(con, secid) for secid in result.secid}
    result["investment_view"] = result.secid.map(
        lambda secid: traces[secid]["investment_view"]["label"]
    )
    result["allocation_view"] = result.secid.map(
        lambda secid: traces[secid]["portfolio_allocation_view"]["label"]
    )
    return result


def render_today():
    st.title("МОЙ ПОРТФЕЛЬ")
    report, frame = _synthesis()
    if report is None or frame.empty:
        _empty()
        return
    if report.stale_warning:
        st.warning(report.stale_warning)
    frame = _add_decision_views(frame)
    daily = _q(
        "SELECT r.canonical_secid,r.total_return FROM daily_returns r "
        "JOIN (SELECT canonical_secid,max(trade_date) trade_date FROM daily_returns GROUP BY 1) x "
        "USING(canonical_secid,trade_date)"
    )
    return_map = dict(zip(daily.canonical_secid, daily.total_return, strict=False))
    daily_change = sum(row.equity_weight * return_map.get(row.secid, 0) for row in frame.itertuples())
    metric = _q(
        "SELECT value FROM portfolio_risk_metrics WHERE snapshot_id=? AND metric='volatility' LIMIT 1",
        [report.portfolio_snapshot_id],
    )
    risk_text = _pct(metric.iloc[0].value) if not metric.empty else "—"
    top = st.columns(6)
    top[0].metric("Стоимость акций", _money(report.total_value))
    top[1].metric("P/L", _pct(report.total_profit_pct))
    top[2].metric("Дневное изменение", _pct(daily_change))
    top[3].metric("Исторический риск", risk_text)
    top[4].metric("Режим рынка", report.market_regime)
    top[5].metric("Актуальность", f"{int(report.data_freshness_days)} дн.")
    st.subheader("СЕГОДНЯШНИЙ ВЫВОД")
    counts = frame.visual_status.value_counts().to_dict()
    cols = st.columns(4)
    filters = [
        ("GREEN", "🟢 Можно рассматривать"),
        ("YELLOW", "🟡 Лучше ждать"),
        ("RED", "🔴 Не увеличивать"),
        ("GRAY", "⚪ Недостаточно данных"),
    ]
    selected = st.session_state.get("today_filter")
    for column, (key, text) in zip(cols, filters, strict=True):
        count = counts.get(key, 0) + (counts.get("LIGHT_GREEN", 0) if key == "GREEN" else 0)
        if column.button(f"{text}: {count}", use_container_width=True):
            st.session_state.today_filter = None if selected == key else key
            st.rerun()
    shown = frame
    if selected:
        allowed = [selected, "LIGHT_GREEN"] if selected == "GREEN" else [selected]
        shown = frame[frame.visual_status.isin(allowed)]
    st.dataframe(_human_table(shown), use_container_width=True, hide_index=True)
    alerts = frame[(frame.visual_status.isin(["RED", "ORANGE", "GRAY"])) | (frame.risk_contribution > 0.3)]
    if not alerts.empty:
        st.subheader("ВАЖНО")
        for row in alerts.head(5).itertuples():
            reason = row.top_negative or row.risk_view
            st.warning(f"{status_label(row.visual_status)} · {row.secid}: {reason}")
    st.subheader("🟢 МОЖНО РАССМАТРИВАТЬ")
    candidates = frame[frame.visual_status.isin(["GREEN", "LIGHT_GREEN"])]
    if candidates.empty:
        st.info("Сейчас нет позиций с достаточным подтверждением для зелёного статуса.")
    for row in candidates.itertuples():
        with st.container(border=True):
            st.markdown(f"### {row.secid} · {_money(row.current_price)}")
            st.write(status_label(row.visual_status))
            st.caption(f"Вес: {_pct(row.equity_weight)} · Почему: {row.top_positive}")
            st.caption(f"Главный риск: {row.top_negative} · Следующий транш: до 10% очередного пополнения")
    if st.button("Обновить анализ сейчас"):
        _run_recalculation()
    live = _q("SELECT count(*) matured FROM forecast_outcomes WHERE outcome_status='matured'")
    matured = int(live.iloc[0].matured) if not live.empty else 0
    st.caption(
        f"Модель проверена на {matured} live-прогнозах. "
        + ("Live-история пока накапливается." if matured < 20 else "См. качество прогнозов.")
    )


def render_portfolio():
    st.header("Мой портфель")
    report, frame = _synthesis()
    if report is None or frame.empty:
        _empty()
        return
    st.dataframe(_human_table(frame), use_container_width=True, hide_index=True)
    st.subheader("Исследовательский рейтинг — без магического общего score")
    blocks = _q(
        "SELECT secid,block_id,score,confidence,status FROM human_intelligence_blocks WHERE report_id=?",
        [report.report_id],
    )
    names = {
        "portfolio_fit": "Соответствие портфелю",
        "business_quality": "Фундаментал",
        "valuation": "Оценка",
        "dividend_outlook": "Дивиденд",
        "technical_state": "Timing",
        "volatility_risk": "Риск",
        "data_quality": "Качество данных",
    }
    blocks = blocks[blocks.block_id.isin(names)].copy()
    blocks["Критерий"] = blocks.block_id.map(names)
    pivot = blocks.pivot(index="secid", columns="Критерий", values="score").reset_index()
    st.dataframe(pivot, use_container_width=True, hide_index=True)
    _render_editor()


def _run_recalculation():
    try:
        with connection() as con:
            recalculate_portfolio(con)
    except Exception as exc:
        st.error(f"Пересчёт не завершён: {type(exc).__name__}: {exc}")
    else:
        st.success("Портфель и все страницы анализа обновлены")
        st.rerun()


def _editor_frame(rows):
    return pd.DataFrame(
        [
            {
                "Тикер": x["secid"],
                "Количество": x["quantity"],
                "Средняя цена": x["average_price"],
                "Разрешить покупку": x.get("allow_buy", True),
                "Разрешить сокращение": x.get("allow_sell", True),
                "Заморожено": x.get("frozen", False),
                "Комментарий": x.get("notes", ""),
            }
            for x in rows
        ]
    )


def _from_editor(frame):
    return [
        {
            "secid": x.get("Тикер", ""),
            "quantity": x.get("Количество"),
            "average_price": x.get("Средняя цена"),
            "allow_buy": x.get("Разрешить покупку", True),
            "allow_sell": x.get("Разрешить сокращение", True),
            "frozen": x.get("Заморожено", False),
            "notes": x.get("Комментарий", ""),
        }
        for x in frame.to_dict("records")
    ]


def _render_editor():
    st.divider()
    st.subheader("Редактировать портфель")
    original = load_positions()
    edited = st.data_editor(
        _editor_frame(original),
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key="portfolio_editor",
    )
    candidate = _from_editor(edited)
    try:
        with connection(read_only=True) as con:
            names = instrument_registry(con)
        normalized = validate_positions(candidate, set(names))
        changes = position_diff(original, normalized)
    except ValueError as exc:
        normalized, changes = [], []
        st.error(str(exc))
    if changes:
        st.markdown("**Изменения перед сохранением:**")
        for change in changes:
            st.write(f"• {change}")
    left, right = st.columns(2)
    if left.button("Сбросить изменения", use_container_width=True):
        st.session_state.pop("portfolio_editor", None)
        st.rerun()
    if right.button(
        "Сохранить и пересчитать", type="primary", disabled=not changes, use_container_width=True
    ):
        try:
            save_positions(normalized, set(names))
            with connection() as con:
                recalculate_portfolio(con)
        except Exception as exc:
            st.error(f"Сохранение не завершено: {type(exc).__name__}: {exc}")
        else:
            st.session_state.pop("portfolio_editor", None)
            st.success("Сохранено атомарно; backup создан; анализ обновлён")
            st.rerun()


def _company_card(row, report_id):
    st.subheader(f"{row.secid} — {_money(row.current_price)}")
    cols = st.columns(6)
    cols[0].metric("Количество", f"{row.quantity:g}")
    cols[1].metric("Средняя цена", _money(row.average_price))
    cols[2].metric("Стоимость", _money(row.quantity * row.current_price))
    cols[3].metric("Вес", _pct(row.equity_weight))
    cols[4].metric("P/L", _pct(row.profit_loss_pct))
    cols[5].metric("Уверенность", confidence_dots(row.confidence_label))
    with connection(read_only=False) as con:
        trace = explain_current_decision(con, row.secid)
    st.markdown(f"### Инвестиционная оценка: {trace['investment_view']['label']}")
    st.markdown(f"### Портфельное ограничение: {trace['portfolio_allocation_view']['label']}")
    st.caption(f"Общий action status: {status_label(row.visual_status)}")
    st.caption(row.status_change)
    items = [
        ("БЛИЖАЙШИЕ ДНИ", horizon_label(row.short_term_view)),
        ("МЕСЯЦ", horizon_label(row.medium_term_view)),
        ("3–12 МЕСЯЦЕВ", horizon_label(row.long_term_view)),
        ("ФУНДАМЕНТАЛ", row.portfolio_view),
        ("ОЦЕНКА", row.valuation_view),
        ("ДИВИДЕНД", row.dividend_view),
        ("РИСК", row.risk_view),
        ("ВЕС В ПОРТФЕЛЕ", "🔴 Высокий" if row.equity_weight >= 0.3 else "🔵 Допустимый"),
    ]
    for column, (title, value) in zip(st.columns(4) * 2, items, strict=True):
        with column:
            st.markdown(f"**{title}**")
            st.write(value)
    horizons = _q(
        "SELECT horizon,view_text FROM human_horizon_views WHERE report_id=? AND secid=? ORDER BY horizon",
        [report_id, row.secid],
    )
    if not horizons.empty:
        st.dataframe(
            pd.DataFrame(
                [horizons.view_text.tolist()],
                columns=["1 день", "5 дней", "1 месяц", "3 месяца", "6 месяцев", "1 год"],
            ),
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Почему программа так считает?"):
        positive, negative = st.columns(2)
        with positive:
            st.markdown("**ЗА**")
            for item in _loads(row.evidence_for_json):
                st.markdown(f"🟢 {item}")
        with negative:
            st.markdown("**ПРОТИВ**")
            for item in _loads(row.evidence_against_json):
                st.markdown(f"🔴 {item}")
        st.markdown("**Что должно измениться**")
        for item in _loads(row.invalidation_json):
            st.markdown(f"🟡 {item}")
    with st.expander("Показать расчёты"):
        details = _q(
            "SELECT block_id,score,confidence,status,freshness_days,source_count,methodology_version "
            "FROM human_intelligence_blocks WHERE report_id=? AND secid=? ORDER BY block_id",
            [report_id, row.secid],
        )
        st.dataframe(details, use_container_width=True, hide_index=True)
    with st.expander("История прогнозов"):
        history = _q(
            "SELECT r.cutoff Дата,r.horizon_sessions Горизонт,r.qualitative_direction Прогноз,"
            'r.current_price "Цена тогда",o.actual_close "Цена потом",o.actual_return Доходность,'
            "coalesce(cast(o.direction_correct AS VARCHAR),o.outcome_status) Результат,"
            "r.model_version Версия "
            "FROM forecast_registry r LEFT JOIN forecast_outcomes o USING(forecast_id) "
            "WHERE r.secid=? ORDER BY r.cutoff DESC,r.horizon_sessions",
            [row.secid],
        )
        if history.empty:
            st.info("История начнёт накапливаться после ежедневного capture.")
        else:
            st.dataframe(history, use_container_width=True, hide_index=True)


def _allocation_inputs(frame):
    specs = _q("SELECT secid,lot_size FROM portfolio_instruments")
    lot_sizes = dict(zip(specs.secid, specs.lot_size, strict=False)) if not specs.empty else {}
    return [
        {
            "secid": row.secid,
            "status": row.visual_status,
            "price": row.current_price,
            "weight": row.equity_weight,
            "risk_contribution": row.risk_contribution,
            "confidence": row.confidence_label,
            "reason": row.top_positive,
            "risk": row.top_negative,
            "lot_size": lot_sizes.get(row.secid),
            "liquidity_ok": row.data_status in {"sufficient", "validated_current"}
            and pd.notna(lot_sizes.get(row.secid)),
        }
        for row in frame.itertuples()
    ]


def render_allocation():
    st.header("Куда вложить пополнение")
    st.warning("Это исследовательский план, а не брокерская заявка. Полное размещение не обязательно.")
    report, frame = _synthesis()
    if report is None or frame.empty:
        _empty()
        return
    quick = st.columns(5)
    for column, amount in zip(quick, (50_000, 100_000, 250_000, 500_000, 1_000_000), strict=True):
        if column.button(f"{amount // 1000} тыс.", use_container_width=True):
            st.session_state.allocation_amount = amount
    amount = st.number_input(
        "Сумма пополнения, ₽",
        min_value=1.0,
        value=float(st.session_state.get("allocation_amount", 100_000)),
        step=10_000.0,
    )
    plan = plan_allocation(amount, _allocation_inputs(frame))
    summary = st.columns(2)
    summary[0].metric("Допустимо распределить сейчас", _money(plan.invested))
    summary[1].metric("Оставить нераспределённым", _money(plan.reserve))
    if not plan.rows:
        st.info("Система не видит достаточно привлекательных вариантов для полного размещения суммы сейчас.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Акция": x["secid"],
                        "Сумма": _money(x["amount"]),
                        "Лотов": x["lots"],
                        "Акций": x["quantity"],
                        "Доля пополнения": _pct(x["allocation_share"]),
                        "Почему": x["reason"],
                        "Риск": x["risk"],
                    }
                    for x in plan.rows
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    excluded = frame[~frame.visual_status.isin(["GREEN", "LIGHT_GREEN"])]
    with st.expander("Почему остальные позиции исключены"):
        for row in excluded.itertuples():
            st.write(f"{status_label(row.visual_status)} · {row.secid}: {row.top_negative}")


def render_stocks():
    st.header("Акции")
    report, frame = _synthesis()
    if report is None or frame.empty:
        _empty()
        return
    secid = st.selectbox("Выберите акцию", frame.secid.tolist())
    _company_card(frame[frame.secid == secid].iloc[0], report.report_id)


def render_ask():
    st.header("Спросить про портфель")
    st.caption(
        "Ответы строятся только из сохранённых расчётов. "
        "Интернет и свободная генерация фактов не используются."
    )
    examples = [
        "Что сейчас лучше докупить на 100 тысяч?",
        "Что сейчас можно пополнить?",
        "Что самое рискованное?",
        "Что по Сберу?",
        "Что по Лукойлу?",
        "Где лучший дивиденд?",
        "Что будет при IMOEX −15%?",
        "Почему сейчас лучше ждать?",
    ]
    selected = st.selectbox("Вопрос", examples + [q for q in INTENTS if q not in examples])
    if st.button("Получить ответ", type="primary"):
        if "100 тысяч" in selected:
            report, frame = _synthesis()
            if report is None or frame.empty:
                _empty()
                return
            plan = plan_allocation(100_000, _allocation_inputs(frame))
            if plan.rows:
                st.markdown("### Допустим только частичный поэтапный транш")
                for row in plan.rows:
                    st.markdown(
                        f"**{row['secid']}** — {_money(row['amount'])}, "
                        f"{row['lots']} лотов / {row['quantity']} акций"
                    )
                    st.caption(
                        f"Почему: {row['reason']} · Риск: {row['risk']} · Уверенность: {row['confidence']}"
                    )
            else:
                st.markdown("### Сейчас нет достаточно подтверждённых кандидатов")
            st.info(f"Оставить в резерве: {_money(plan.reserve)}")
            st.caption(f"Данные на: {report.analysis_cutoff}. Только сохранённая аналитика.")
            return
        with connection(read_only=False) as con:
            answer = answer_question(con, selected)
        st.markdown(f"### {answer['conclusion']}")
        st.caption(f"Уверенность: {answer['confidence']} · Данные на: {answer['data_cutoff']}")
        if answer["supporting_evidence"]:
            st.markdown("**За:** " + "; ".join(answer["supporting_evidence"]))
        if answer["opposing_evidence"]:
            st.markdown("**Против:** " + "; ".join(answer["opposing_evidence"]))


def render_dividends():
    st.header("Дивиденды")
    report = _latest_report()
    if report is None:
        _empty()
        return
    frame = _q(
        "SELECT secid,scenario,month,net,dps,yield_current,status,confidence FROM portfolio_dividend_outlook "
        "WHERE snapshot_id=? ORDER BY month,secid,scenario",
        [report.portfolio_snapshot_id],
    )
    if frame.empty:
        _empty()
        return
    confirmed = frame[frame.status.str.contains("confirmed", case=False, na=False)]
    estimated = frame[~frame.index.isin(confirmed.index)]
    st.subheader("CONFIRMED")
    st.info("Подтверждённых будущих выплат в текущем наборе нет") if confirmed.empty else st.dataframe(
        confirmed
    )
    st.subheader("ESTIMATED")
    st.warning("Сценарии не являются объявленными дивидендами")
    st.dataframe(estimated, use_container_width=True, hide_index=True)


def render_risks():
    st.header("Риски")
    report, frame = _synthesis()
    if report is None or frame.empty:
        _empty()
        return
    risk = _q(
        "SELECT replace(factor,'risk_contribution:','') secid,exposure FROM portfolio_factor_exposures "
        "WHERE snapshot_id=? AND factor LIKE 'risk_contribution:%' ORDER BY exposure DESC",
        [report.portfolio_snapshot_id],
    )
    st.markdown("### Основной риск портфеля сейчас создают:")
    for number, row in enumerate(risk.head(3).itertuples(), 1):
        st.markdown(f"{number}. **{row.secid} — {row.exposure:.2%}**")
    if not risk.empty:
        st.warning(
            f"Три крупнейших вклада создают около {risk.head(3).exposure.sum():.0%} "
            "исторического риска акционной части."
        )
    st.dataframe(
        frame[["secid", "equity_weight", "risk_view", "portfolio_view", "confidence_label"]],
        use_container_width=True,
        hide_index=True,
    )


def render_scenarios():
    st.header("Сценарии")
    st.warning("Сценарий — не прогноз и не вероятность будущего результата.")
    report = _latest_report()
    if report is None:
        _empty()
        return
    frame = _q(
        "SELECT secid,scenario,mechanical_sensitivity,range_low,range_high,confidence,"
        "structural_break_warning "
        "FROM portfolio_scenarios_v2 WHERE snapshot_id=? ORDER BY scenario,secid",
        [report.portfolio_snapshot_id],
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)


def render_update():
    st.header("Обновить данные")
    st.info("Быстрое — ежедневно. Глубокое — периодически. Переобучение — редко и без автопродвижения.")
    from moex_analytics.portfolio_research.daily_governance import run_daily_update

    actions = (
        ("🟢 Быстрое ежедневное обновление", "quick", False),
        ("🔵 Глубокое обновление", "deep", False),
        ("🟠 Исследовать/переобучить модели", "retrain", True),
    )
    for label, mode, dry_run in actions:
        if st.button(label, use_container_width=True):
            progress = st.progress(0, text="Подготовка")
            labels = [
                "Цены",
                "Макро",
                "Фундаментал",
                "Дивиденды",
                "Режимы",
                "Портфель",
                "Прогнозы",
                "Проверка старых прогнозов",
            ]
            for index, name in enumerate(labels, 1):
                progress.progress(index / 8, text=f"{index}/8 {name}")
            with connection() as con:
                result = run_daily_update(con, mode=mode, dry_run=dry_run)
            st.success(f"Обновление завершено: {result['status']}")
            st.json(
                {
                    key: result[key]
                    for key in (
                        "duration_seconds",
                        "sources_checked",
                        "http_requests",
                        "rows_inserted",
                        "errors",
                        "new_forecasts",
                        "matured_forecasts",
                    )
                }
            )
