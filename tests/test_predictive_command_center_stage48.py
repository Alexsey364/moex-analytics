import duckdb

from moex_analytics.dashboard.pages.predictive_command_center import (
    HORIZONS,
    _heatmap,
    load_command_center,
)
from moex_analytics.fusion_engine.core import ensure_schema as ensure_fusion_schema


def test_command_center_reads_stored_results_only() -> None:
    con = duckdb.connect(":memory:")
    ensure_fusion_schema(con)
    con.execute(
        "CREATE TABLE regime_timeline_v2("
        "regime INTEGER,novelty_status VARCHAR,trade_date DATE,selected BOOLEAN)"
    )
    assert load_command_center(con) == {"ready": False}
    con.execute("INSERT INTO predictive_fusion_runs VALUES ('r','e',now(),now(),'completed',0,1,'v','{}')")
    con.execute(
        "INSERT INTO current_fusion_research VALUES "
        "('r','SBERP',20,DATE '2024-01-01','positive',.01,'low',FALSE,NULL,'{}','shadow',TRUE,FALSE)"
    )
    con.execute("INSERT INTO regime_timeline_v2 VALUES (1,'familiar',DATE '2024-01-01',TRUE)")
    result = load_command_center(con)
    assert result["ready"]
    assert result["current"].iloc[0].secid == "SBERP"
    assert result["current"].iloc[0].abstained == False  # noqa: E712


def test_heatmap_has_all_requested_horizons() -> None:
    import pandas as pd

    frame = pd.DataFrame({
        "secid": ["SBERP"] * len(HORIZONS), "horizon": HORIZONS,
        "signal": ["positive"] * len(HORIZONS), "abstained": [False] * len(HORIZONS),
    })
    figure = _heatmap(frame)
    assert list(figure.data[0].x) == [f"{horizon}d" for horizon in HORIZONS]
    assert figure.data[0].text[0][0] == "🟢 ↑"
