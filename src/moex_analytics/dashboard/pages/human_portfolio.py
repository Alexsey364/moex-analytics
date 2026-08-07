"""Russian, three-layer portfolio dashboard backed by deterministic daily reports."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from moex_analytics.database import connection
from moex_analytics.portfolio_research.human_intelligence import INTENTS, answer_question


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
    return report, frame


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
    return pd.DataFrame(
        {
            "Акция": frame.secid,
            "Цена": frame.current_price.map(_money),
            "Мой вес": frame.equity_weight.map(_pct),
            "Ближайшие дни": frame.short_term_view,
            "Месяц": frame.medium_term_view,
            "Перспектива": frame.long_term_view,
            "Дивиденд": frame.dividend_view,
            "Риск": frame.risk_view,
            "Что делать": frame.timing_view,
            "Уверенность": frame.confidence_label,
        }
    )


def render_today():
    st.title("АНАЛИТИКА МОЕГО ПОРТФЕЛЯ")
    report, frame = _synthesis()
    if report is None or frame.empty:
        _empty()
        return
    if report.stale_warning:
        st.warning(report.stale_warning)
    top = st.columns(6)
    top[0].metric("Дата анализа", str(report.analysis_cutoff))
    top[1].metric("Данные актуальны", f"{int(report.data_freshness_days)} дн.")
    top[2].metric("Режим рынка", report.market_regime)
    top[3].metric("Акционная часть", _money(report.total_value))
    top[4].metric("Результат", _pct(report.total_profit_pct))
    risky = int((frame.action_group == "do_not_increase").sum())
    top[5].metric("Ограничено риском", risky)
    st.subheader("Коротко")
    counts = frame.action_group.value_counts().to_dict()
    cols = st.columns(4)
    cols[0].info(f"{counts.get('consider', 0)} позиций допустимы для небольшого пополнения")
    cols[1].info(f"{counts.get('wait', 0)} позиций лучше пока наблюдать")
    cols[2].warning(f"{counts.get('do_not_increase', 0)} позиций ограничены концентрацией")
    cols[3].info(f"{counts.get('insufficient_data', 0)} позиций имеют недостаточно данных")
    st.subheader("Главная таблица")
    st.dataframe(_human_table(frame), use_container_width=True, hide_index=True)
    st.subheader("Приоритеты")
    labels = {
        "consider": "Можно рассматривать для пополнения",
        "wait": "Лучше подождать",
        "do_not_increase": "Не увеличивать сейчас",
        "insufficient_data": "Недостаточно данных",
    }
    for group, label in labels.items():
        with st.container(border=True):
            st.markdown(f"### {label}")
            subset = frame[frame.action_group == group]
            if subset.empty:
                st.caption("Позиций нет")
            for row in subset.itertuples():
                st.markdown(f"**{row.secid}** — {row.timing_view}")
                st.caption(f"Причина: {row.top_negative if group != 'consider' else row.top_positive}")


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


def _company_card(row, report_id):
    st.subheader(f"{row.secid} — {_money(row.current_price)}")
    cols = st.columns(4)
    cols[0].metric("Количество", f"{row.quantity:g}")
    cols[1].metric("Вес", _pct(row.equity_weight))
    cols[2].metric("P/L", _pct(row.profit_loss_pct))
    cols[3].metric("Уверенность", row.confidence_label)
    st.markdown(f"**Сейчас:** {row.timing_view}")
    st.markdown(f"**Ближайшие дни:** {row.short_term_view}")
    st.markdown(f"**Месяц:** {row.medium_term_view}")
    st.markdown(f"**3–12 месяцев:** {row.long_term_view}")
    st.markdown(f"**Оценка:** {row.valuation_view}")
    st.markdown(f"**Дивиденд:** {row.dividend_view}")
    st.markdown(f"**Риск:** {row.risk_view}")
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
        st.markdown("**За**")
        for item in _loads(row.evidence_for_json):
            st.markdown(f"+ {item}")
        st.markdown("**Против**")
        for item in _loads(row.evidence_against_json):
            st.markdown(f"- {item}")
        st.markdown("**Что может изменить вывод**")
        for item in _loads(row.invalidation_json):
            st.markdown(f"- {item}")
    with st.expander("Показать расчёты"):
        details = _q(
            "SELECT block_id,score,confidence,status,freshness_days,source_count,methodology_version "
            "FROM human_intelligence_blocks WHERE report_id=? AND secid=? ORDER BY block_id",
            [report_id, row.secid],
        )
        st.dataframe(details, use_container_width=True, hide_index=True)


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
        with connection(read_only=True) as con:
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
    st.info("Для полного обновления закройте другие процессы, использующие базу.")
    if st.button("Запустить ежедневный анализ", type="primary"):
        from moex_analytics.portfolio_research.human_intelligence import run_daily_intelligence

        with connection() as con:
            result = run_daily_intelligence(con)
        st.success(result["message"])
        for warning in result["warnings"]:
            st.warning(warning)
