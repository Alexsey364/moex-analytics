"""Dashboard pages for the point-in-time macro experiment."""

import json

import pandas as pd
import streamlit as st

from ...config import load_instruments
from ...database import connection


def _ticker():
    return st.selectbox("Инструмент", [item["secid"] for item in load_instruments()])


def render_macro():
    st.header("Макроэкономика")
    with connection() as con:
        series = con.execute("""SELECT s.name,s.source,s.is_point_in_time_safe,
            max(o.observation_date) observation_date,max(o.release_date) release_date,
            arg_max(o.value,o.available_from) current_value,s.unit,s.notes
            FROM macro_series s LEFT JOIN macro_observations o USING(series_id)
            GROUP BY s.series_id,s.name,s.source,s.is_point_in_time_safe,s.unit,s.notes
            ORDER BY s.name""").fetchdf()
        issues = con.execute("""SELECT series_id,issue_type,description,severity
            FROM macro_quality_issues ORDER BY detected_at DESC LIMIT 30""").fetchdf()
    st.dataframe(series, use_container_width=True)
    if not issues.empty:
        st.warning("Обнаружены проблемы качества; сомнительные значения не исправляются автоматически.")
        st.dataframe(issues, use_container_width=True)


def render_instrument_factors():
    st.header("Макрофакторы инструмента")
    ticker = _ticker()
    with connection() as con:
        row = con.execute(
            """SELECT trade_date,features_json,source_dates_json,available_at
            FROM macro_features WHERE canonical_secid=? ORDER BY trade_date DESC LIMIT 1""",
            [ticker],
        ).fetchone()
    if not row:
        st.warning("Макрофакторы ещё не рассчитаны.")
        return
    factors = pd.DataFrame([{"Фактор": key, "Значение": value} for key, value in json.loads(row[1]).items()])
    st.caption(f"Срез {row[0]}, доступность {row[3]}")
    st.dataframe(factors, use_container_width=True)
    with st.expander("Исходные даты наблюдений"):
        st.json(json.loads(row[2]))


def render_forecasts():
    st.header("Прогнозные диапазоны")
    ticker = _ticker()
    with connection() as con:
        frame = con.execute(
            """SELECT horizon,current_price,median_return,
            lower_price_50,upper_price_50,lower_price_80,upper_price_80,
            lower_price_90,upper_price_90,positive_frequency,model_quality,baseline,as_of_date
            FROM forecast_ranges WHERE canonical_secid=? ORDER BY horizon""",
            [ticker],
        ).fetchdf()
    if frame.empty:
        st.warning("Диапазоны ещё не рассчитаны.")
        return
    st.dataframe(frame, use_container_width=True)
    st.warning("Исторически оценённый диапазон при текущей модели — не обещание будущей цены.")


def render_comparison():
    st.header("Сравнение моделей")
    ticker = _ticker()
    with connection() as con:
        frame = con.execute(
            """SELECT horizon,model_type,fold,period,train_end,test_start,
            test_end,metrics_json FROM macro_model_results WHERE canonical_secid=?
            ORDER BY horizon,period,fold,model_type""",
            [ticker],
        ).fetchdf()
    if frame.empty:
        st.warning("Walk-forward проверка ещё не выполнена.")
        return
    metrics = pd.json_normalize(frame.pop("metrics_json").map(json.loads))
    st.dataframe(pd.concat([frame, metrics], axis=1), use_container_width=True)
    st.info("technical-v1 не перенастраивался по старому out-of-sample периоду.")


def render_events():
    st.header("Календарь событий")
    with connection() as con:
        frame = con.execute("""SELECT event_type,related_instrument,scheduled_date,
            actual_release_at,status,importance,source,notes FROM event_calendar
            ORDER BY scheduled_date""").fetchdf()
    if frame.empty:
        st.warning("Нет событий с подтверждённым официальным расписанием в локальной базе.")
        return
    st.dataframe(frame, use_container_width=True)


def render_audit():
    st.header("Аудит макромодели")
    ticker = _ticker()
    with connection() as con:
        latest = con.execute("""SELECT run_id,duration_seconds,finished_at
            FROM macro_audit_runs ORDER BY finished_at DESC LIMIT 1""").fetchone()
        if not latest:
            st.warning("Аудит ещё не выполнен. Запустите audit-macro-model.")
            return
        run_id = latest[0]
        data = con.execute(
            """SELECT series_id,metrics_json FROM macro_data_audit
            WHERE run_id=? AND canonical_secid=? ORDER BY series_id""",
            [run_id, ticker],
        ).fetchdf()
        matrix = con.execute(
            """SELECT horizon,metrics_json FROM macro_matrix_audit
            WHERE run_id=? AND canonical_secid=? ORDER BY horizon""",
            [run_id, ticker],
        ).fetchdf()
        ablation = con.execute(
            """SELECT horizon,block_name,sample_type,period,metrics_json
            FROM macro_ablation_results WHERE run_id=? AND canonical_secid=?
            ORDER BY horizon,block_name,sample_type""",
            [run_id, ticker],
        ).fetchdf()
        coefficients = con.execute(
            """SELECT horizon,block_name,feature_name,metrics_json
            FROM macro_coefficient_audit WHERE run_id=? AND canonical_secid=?
            ORDER BY horizon,block_name,feature_name""",
            [run_id, ticker],
        ).fetchdf()
        regimes = con.execute(
            """SELECT horizon,regime,metrics_json FROM macro_regime_audit
            WHERE run_id=? AND canonical_secid=? ORDER BY horizon,regime""",
            [run_id, ticker],
        ).fetchdf()
        decisions = con.execute(
            """SELECT horizon,block_name,status,reason,evidence_json
            FROM macro_feature_audit WHERE run_id=? AND canonical_secid=?
            ORDER BY horizon,block_name""",
            [run_id, ticker],
        ).fetchdf()
    st.caption(f"Запуск {run_id}; длительность {latest[1]:.1f} с; завершён {latest[2]}")
    tabs = st.tabs(["Заполненность и возраст", "Матрица", "Ablation", "Коэффициенты", "Режимы", "Решения"])
    frames = (data, matrix, ablation, coefficients, regimes, decisions)
    for tab, frame in zip(tabs, frames, strict=True):
        with tab:
            for column in ("metrics_json", "evidence_json"):
                if column in frame:
                    normalized = pd.json_normalize(frame.pop(column).map(json.loads))
                    frame = pd.concat([frame, normalized], axis=1)
            for column in frame.select_dtypes(include="object"):
                frame[column] = frame[column].map(
                    lambda value: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else "" if pd.isna(value) else str(value)
                )
            st.dataframe(frame, use_container_width=True)
    st.warning("Статусы аудита исследовательские и не являются торговой рекомендацией.")
