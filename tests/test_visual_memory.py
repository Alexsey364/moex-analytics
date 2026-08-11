import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest

import moex_analytics.dashboard.pages.market_memory as page
import moex_analytics.visual_memory.core as memory
from moex_analytics.dashboard.pages.market_memory import NAMES, _chart
from moex_analytics.visual_memory.core import (
    MIN_ANALOGS,
    MODES,
    _current_path,
    _scenario_name,
    normalize_t0,
    similarity_label,
)


def _snapshot() -> dict:
    current = [
        {"relative_session": index, "normalized": 100 + index, "real_price": 200 + index}
        for index in range(-2, 1)
    ]
    future = [
        {
            "relative_session": index,
            "normalized": 100 + index,
            "real_price": 200 + index,
            "date": str(date(2020, 1, 1) + timedelta(days=index)),
        }
        for index in range(1, 6)
    ]
    pre = [
        {
            "relative_session": index,
            "normalized": 100 + index,
            "real_price": 200 + index,
            "date": str(date(2019, 12, 31) + timedelta(days=index)),
        }
        for index in range(-2, 1)
    ]
    return {
        "current": current,
        "analogs": [{"date": "2020-01-01", "rank": 1, "similarity": 0.9, "points": pre + future}],
        "bands": [
            {
                "relative_session": index,
                "q10": 98 + index,
                "q25": 99 + index,
                "median": 100 + index,
                "q75": 101 + index,
                "q90": 102 + index,
            }
            for index in range(1, 6)
        ],
        "window": 20,
        "sample": 5,
        "status": "ready",
        "summary": {
            "analogs": 5,
            "above": 3,
            "below": 2,
            "median": 0.02,
            "q10": -0.1,
            "q25": -0.03,
            "q75": 0.06,
            "q90": 0.12,
            "median_drawdown": -0.04,
        },
        "cards": [
            {
                "date": "2020-01-01",
                "rank": 1,
                "similarity": 0.9,
                "similarity_label": "Очень похоже",
                "regime_similar": True,
                "scenario": "Рост",
                "returns": {"5": 0.01, "20": 0.02},
                "max_drawdown": -0.03,
            }
        ],
        "why": {"Акция": "похоже", "Ставки": "частично"},
        "scenarios": [
            {
                "scenario": "Рост",
                "episodes": 5,
                "median_return": 0.02,
                "median_drawdown": -0.03,
                "representative_date": "2020-01-01",
            }
        ],
        "cutoff": "2026-08-07",
    }


def test_t0_normalization_and_current_path_end_without_future() -> None:
    prices = {date(2026, 1, 1) + timedelta(days=index): 100 + index for index in range(10)}
    cutoff = date(2026, 1, 8)
    path = _current_path(prices, cutoff, 5)
    assert path[-1]["relative_session"] == 0
    assert path[-1]["normalized"] == pytest.approx(100)
    assert all(point["relative_session"] <= 0 for point in path)
    assert all(point["date"] <= str(cutoff) for point in path)
    assert normalize_t0([50, 75, 100]) == [50, 75, 100]


def test_chart_aligns_real_historical_future_but_never_current_future() -> None:
    figure = _chart(_snapshot(), True)
    current = next(trace for trace in figure.data if trace.name == "СЕЙЧАС")
    analog = next(trace for trace in figure.data if "аналог" in trace.name)
    assert max(current.x) == 0
    assert max(analog.x) == 5
    assert any(shape.x0 == 0 and shape.x1 == 0 for shape in figure.layout.shapes)
    assert any(trace.name == "Медиана реальных аналогов" for trace in figure.data)
    real_price_figure = _chart(_snapshot(), False)
    assert real_price_figure.layout.yaxis.title.text == "Реальная историческая цена, ₽"


def test_similarity_scenarios_modes_independence_and_all_stocks_contract() -> None:
    assert similarity_label(0.9) == "Очень похоже"
    assert similarity_label(0.75) == "Похоже"
    assert similarity_label(0.55) == "Среднее сходство"
    assert similarity_label(0.2) == "Слабое сходство"
    assert _scenario_name("dip_then_recover") == "Просадка с восстановлением"  # noqa: RUF001
    assert MODES["price_path"] == ("path_cosine", 20)
    assert MIN_ANALOGS == 5
    assert set(NAMES) == {"X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX"}


def test_basic_view_hides_technical_table_by_default() -> None:
    source = Path("src/moex_analytics/dashboard/pages/market_memory.py").read_text(encoding="utf-8")
    assert 'with st.expander("Технические детали")' in source
    assert "Что происходило после похожих ситуаций" in source
    assert "Прогноз" not in source


def _fake_streamlit() -> MagicMock:
    fake = MagicMock()
    fake.columns.side_effect = lambda spec: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    fake.container.return_value.__enter__.return_value = MagicMock()
    fake.expander.return_value.__enter__.return_value = MagicMock()
    return fake


def test_ready_helpers_and_basic_renderer_are_human_readable(monkeypatch) -> None:
    snapshot = _snapshot()
    fake = _fake_streamlit()
    monkeypatch.setattr(page, "st", fake)
    page._summary(snapshot, "1 месяц")
    page._cards(snapshot)
    page._why(snapshot)
    page._scenarios(snapshot)
    monkeypatch.setattr(page, "_load_snapshot", lambda *_args: snapshot)
    page.render_basic_analogs("SBERP")
    assert fake.metric.call_count == 0  # metrics are emitted through the five columns
    assert fake.info.call_count == 0
    assert fake.caption.call_count >= 3


def test_empty_and_small_sample_states_do_not_overclaim(monkeypatch) -> None:
    fake = _fake_streamlit()
    monkeypatch.setattr(page, "st", fake)
    snapshot = _snapshot() | {"sample": 2, "scenarios": []}
    page._summary(snapshot, "1 неделя")
    page._scenarios(snapshot)
    monkeypatch.setattr(page, "_load_snapshot", lambda *_args: None)
    page.render_basic_analogs("X5")
    assert fake.info.call_count == 3


def test_visual_memory_build_persists_real_paths_and_is_idempotent(monkeypatch) -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE scenario_research_runs(run_id VARCHAR,analog_run_id VARCHAR,"
        "trajectory_run_id VARCHAR,cutoff DATE,status VARCHAR,finished_at TIMESTAMP)"
    )
    con.execute("INSERT INTO scenario_research_runs VALUES ('s','a','t','2026-08-07','completed',now())")
    con.execute("CREATE TABLE canonical_daily_prices(canonical_secid VARCHAR,trade_date DATE,close DOUBLE)")
    con.execute(
        "CREATE TABLE scenario_multiscale_matches(run_id VARCHAR,secid VARCHAR,method VARCHAR,"
        "analog_date DATE,similarity_score DOUBLE,regime_agreement BOOLEAN,event_agreement BOOLEAN,"
        "applicability VARCHAR,gaps_json VARCHAR,independent BOOLEAN,combined_distance DOUBLE)"
    )
    con.execute(
        "CREATE TABLE scenario_prehistory_points(run_id VARCHAR,secid VARCHAR,method VARCHAR,"
        "analog_date DATE,series_type VARCHAR,path_window INTEGER,relative_session INTEGER,"
        "source_trade_date DATE,normalized_value DOUBLE)"
    )
    con.execute(
        "CREATE TABLE analog_forward_trajectories(run_id VARCHAR,secid VARCHAR,method VARCHAR,"
        "analog_date DATE,path_window INTEGER,forward_session INTEGER,source_trade_date DATE,"
        "normalized_price DOUBLE,forward_return DOUBLE)"
    )
    con.execute(
        "CREATE TABLE scenario_episodes(run_id VARCHAR,secid VARCHAR,method VARCHAR,analog_date DATE,"
        "horizon INTEGER,terminal_return DOUBLE,scenario VARCHAR,max_adverse DOUBLE,max_favorable DOUBLE)"
    )
    con.execute(
        "CREATE TABLE scenario_tree_summaries(run_id VARCHAR,secid VARCHAR,method VARCHAR,horizon INTEGER,"
        "subset VARCHAR,scenario VARCHAR,episodes INTEGER,median_return DOUBLE,median_adverse DOUBLE,"
        "medoid_analog_date DATE,applicability VARCHAR,status VARCHAR)"
    )
    price_rows = [("AAA", date(2026, 7, 1) + timedelta(days=i), 100 + i) for i in range(38)]
    for analog_index in range(5):
        t0 = date(2020 + analog_index, 1, 31)
        con.execute(
            "INSERT INTO scenario_multiscale_matches VALUES ('s','AAA','path_cosine',?,?,TRUE,TRUE,"
            "'applicable','[]',TRUE,?)",
            [t0, 0.9 - analog_index / 100, analog_index / 10],
        )
        for relative in range(-20, 1):
            source_date = t0 + timedelta(days=relative)
            price_rows.append(("AAA", source_date, 200 + relative + analog_index))
            con.execute(
                "INSERT INTO scenario_prehistory_points VALUES ('s','AAA','path_cosine',?,'issuer',20,?,?,?)",
                [t0, relative, source_date, 100 + relative / 10],
            )
        for forward in range(1, 6):
            source_date = t0 + timedelta(days=forward)
            price_rows.append(("AAA", source_date, 200 + forward + analog_index))
            con.execute(
                "INSERT INTO analog_forward_trajectories VALUES ('t','AAA','path_cosine',?,20,?,?,?,?)",
                [t0, forward, source_date, 100 + forward, forward / 100],
            )
        con.execute(
            "INSERT INTO scenario_episodes VALUES "
            "('s','AAA','path_cosine',?,5,.05,'growth_without_deep_drawdown',-.02,.08)",
            [t0],
        )
    con.executemany("INSERT INTO canonical_daily_prices VALUES (?,?,?)", price_rows)
    con.execute(
        "INSERT INTO scenario_tree_summaries VALUES "
        "('s','AAA','path_cosine',5,'all','growth_without_deep_drawdown',5,.05,-.02,"
        "'2020-01-31','applicable','ready')"
    )
    monkeypatch.setattr(memory, "SECIDS", ("AAA",))
    monkeypatch.setattr(memory, "HORIZONS", (5,))
    monkeypatch.setattr(memory, "MODES", {"price_path": ("path_cosine", 20)})
    first = memory.build_visual_memory(con)
    second = memory.build_visual_memory(con)
    assert first["ready"] == 1 and not first["idempotent"]
    assert second["ready"] == 1 and second["idempotent"]
    row = con.execute(
        "SELECT current_path_json,analog_paths_json,bands_json FROM visual_memory_snapshots"
    ).fetchone()
    current, analogs, bands = (json.loads(value) for value in row)
    assert max(point["relative_session"] for point in current) == 0
    assert max(point["relative_session"] for point in analogs[0]["points"]) == 5
    assert len(bands) == 5
    assert con.execute("SELECT count(*) FROM visual_memory_snapshots").fetchone()[0] == 1
