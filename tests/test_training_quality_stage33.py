import duckdb

from moex_analytics.training_quality.relearning import HORIZONS, MODELS, _freeze_benchmark
from moex_analytics.training_quality.schema import DDL


def test_stage33_uses_existing_model_families_and_freezes_benchmark():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    assert HORIZONS == (5, 20, 60, 120)
    assert MODELS == ("pooled_linear", "pooled_tree", "ranking_ridge")
    first = _freeze_benchmark(con)
    second = _freeze_benchmark(con)
    assert first == second
    assert con.execute("SELECT count(*) FROM clean_relearning_benchmarks").fetchone()[0] == 1
    assert con.execute("SELECT immutable FROM clean_relearning_benchmarks").fetchone()[0] is True
