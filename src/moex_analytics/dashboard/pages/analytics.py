"""Explainable analytics dashboard pages."""

import json

import pandas as pd
import streamlit as st

from ...analogues import summarize_outcomes
from ...config import load_instruments, load_settings
from ...database import connection


def _ticker():
    return st.selectbox("Инструмент", [item["secid"] for item in load_instruments()])


def render_summary():
    st.header("Аналитика инструмента")
    ticker = _ticker()
    with connection() as con:
        row = con.execute(
            """SELECT trade_date,total_score,status,blocks_json,
            positive_factors_json,negative_factors_json,statistics_quality,calculated_at
            FROM instrument_scores WHERE canonical_secid=? ORDER BY trade_date DESC LIMIT 1""",
            [ticker],
        ).fetchone()
        regime = con.execute("SELECT regime FROM market_regimes ORDER BY trade_date DESC LIMIT 1").fetchone()
    if not row:
        st.warning("Недостаточно данных. Выполните calculate-analytics.")
        return
    st.metric("Текущий статус", row[2], f"балл {row[1]:.2f}")
    st.caption(f"Режим IMOEX: {regime[0] if regime else '—'} · расчёт {row[7]}")
    st.bar_chart(pd.Series(json.loads(row[3]), name="Балл блока"))
    st.write("Повысили оценку:", ", ".join(json.loads(row[4])) or "—")
    st.write("Снизили оценку:", ", ".join(json.loads(row[5])) or "—")
    st.info(f"Качество: {row[6]}. Историческая статистика не является гарантией.")


def render_factors():
    st.header("Факторы")
    ticker = _ticker()
    with connection() as con:
        row = con.execute(
            """SELECT trade_date,features_json FROM daily_features
            WHERE canonical_secid=? ORDER BY trade_date DESC LIMIT 1""",
            [ticker],
        ).fetchone()
    if not row:
        st.warning("Факторы ещё не рассчитаны.")
        return
    factors = pd.DataFrame([{"Фактор": key, "Значение": value} for key, value in json.loads(row[1]).items()])
    st.caption(f"Дата состояния: {row[0]}")
    st.dataframe(factors, use_container_width=True)


def render_analogues():
    st.header("Исторические аналоги")
    ticker = _ticker()
    with connection() as con:
        analogues = con.execute(
            """SELECT analogue_date,rank,distance,similarity,regime
            FROM historical_analogue_results WHERE canonical_secid=? ORDER BY rank""",
            [ticker],
        ).fetchdf()
        outcomes = con.execute(
            """SELECT f.condition_date,f.horizon,f.price_return,f.max_drawdown
            FROM forward_returns f JOIN historical_analogue_results a
            ON a.analogue_date=f.condition_date AND a.canonical_secid=f.canonical_secid
            WHERE f.canonical_secid=? AND f.horizon IN (5,20,60,120)""",
            [ticker],
        ).fetchdf()
    st.dataframe(analogues, use_container_width=True)
    for horizon in (5, 20, 60, 120):
        subset = outcomes[outcomes["horizon"] == horizon]
        if not subset.empty:
            st.write(f"{horizon} сессий", summarize_outcomes(subset))
    st.caption("Положительный исход — историческая частота, а не гарантированная вероятность.")


def render_regimes():
    st.header("Режим рынка")
    with connection() as con:
        frame = con.execute("SELECT trade_date,regime,reasons_json FROM market_regimes ORDER BY 1").fetchdf()
    if frame.empty:
        st.warning("Режимы ещё не рассчитаны.")
        return
    st.metric("Текущий режим IMOEX", frame.iloc[-1]["regime"])
    st.write("Причины:", ", ".join(json.loads(frame.iloc[-1]["reasons_json"])))
    st.area_chart(frame.set_index("trade_date")["regime"].astype("category").cat.codes)
    st.dataframe(frame["regime"].value_counts().rename("Наблюдений"))


def render_validation():
    st.header("Проверка модели")
    cfg = load_settings()["analytics"]["validation"]
    with connection() as con:
        frame = con.execute("""SELECT s.trade_date,s.status,f.horizon,f.price_return,f.max_drawdown
            FROM instrument_scores s JOIN forward_returns f
            ON s.trade_date=f.condition_date
              AND s.canonical_secid=f.canonical_secid
              AND s.calculation_version=f.calculation_version
            WHERE f.horizon IN (20,60,120) AND f.price_return IS NOT NULL""").fetchdf()
        runs = con.execute("""SELECT calculation_version,config_hash,duration_seconds,finished_at
            FROM analytics_runs ORDER BY id DESC LIMIT 5""").fetchdf()
    if frame.empty:
        st.warning("Недостаточно данных проверки.")
        return
    dates = pd.to_datetime(frame["trade_date"])
    frame["Период"] = "out-of-sample"
    frame.loc[dates <= pd.Timestamp(cfg["validation_end"]), "Период"] = "validation"
    frame.loc[dates <= pd.Timestamp(cfg["development_end"]), "Период"] = "development"
    report = (
        frame.groupby(["Период", "status", "horizon"])
        .agg(
            observations=("price_return", "count"),
            mean_return=("price_return", "mean"),
            median_return=("price_return", "median"),
            positive_frequency=("price_return", lambda x: (x > 0).mean()),
            mean_drawdown=("max_drawdown", "mean"),
        )
        .reset_index()
    )
    st.dataframe(report, use_container_width=True)
    st.warning("Out-of-sample не использовался для настройки весов. Это не торговая рекомендация.")
    st.dataframe(runs, use_container_width=True)
