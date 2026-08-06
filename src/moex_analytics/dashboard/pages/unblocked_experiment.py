"""Dashboard pages for non-production SBER forecast experiments."""

import streamlit as st

from moex_analytics.database import connection


def _show(title, query):
    st.header(title)
    with connection() as con:
        try:
            st.dataframe(con.execute(query).df(), use_container_width=True)
        except Exception as exc:
            st.info(f"Эксперимент ещё не выполнен: {exc}")


def render_direction():
    _show("Эксперимент направления SBER", "select * from sber_experiment_results order by horizon,dataset_id")


def render_datasets():
    _show("Доступные наборы данных", "select * from sber_modular_samples order by horizon,dataset_id")


def render_samples():
    _show(
        "Модульные common samples",
        "select dataset_id,horizon,date_from,date_to,rows_count,feature_count,observations_per_feature,availability_status from sber_modular_samples order by horizon,dataset_id",
    )


def render_horizons():
    _show("Результаты по горизонтам", "select * from sber_experimental_readiness order by horizon")


def render_calibration():
    _show(
        "Калибровка вероятностей",
        "select dataset_id,horizon,brier,log_loss,ece,calibration_slope,calibration_intercept,status from sber_experiment_results order by horizon,brier",
    )


def render_stability():
    _show(
        "Стабильность признаков", "select * from sber_feature_stability order by horizon,dataset_id,feature"
    )


def render_value():
    _show("Добавочная ценность данных", "select * from sber_modular_ablation order by horizon,dataset_id")


def render_forecast():
    _show("Текущий экспериментальный прогноз", "select * from sber_experimental_forecasts order by horizon")


def render_timing():
    _show(
        "Покупать сейчас или ждать — эксперимент",
        "select * from sber_timing_experiments order by horizon,strategy",
    )


def render_shadow():
    _show(
        "Live shadow forecasts",
        "select * from sber_shadow_forecasts order by created_at desc,horizon,dataset_id",
    )
