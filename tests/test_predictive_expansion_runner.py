from pathlib import Path

import duckdb

import moex_analytics.predictive_expansion.expansion as expansion
from moex_analytics.actual_backfill.schema import DDL as ACTUAL_DDL


def _coverage(securities):
    return {
        "securities": securities,
        "active": securities,
        "inactive": 0,
        "rows": securities * 10,
        "completed_jobs": securities,
    }


def test_equity_expansion_stops_at_target_and_does_not_reseed(monkeypatch, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(ACTUAL_DDL)
    state = {"securities": 549}
    monkeypatch.setattr(
        expansion.market_history,
        "coverage",
        lambda _con, save=False: _coverage(state["securities"]),
    )

    def batch(_con, **_kwargs):
        state["securities"] += 120
        return {
            "run_id": str(state["securities"]),
            "requests": 10,
            "failures": 0,
            "securities_added": 120,
            "rows_inserted": 1200,
            "database_growth": 0,
            "raw_growth": 0,
        }

    monkeypatch.setattr(expansion.market_history, "run_batch", batch)
    fake_db = tmp_path / "test.duckdb"
    fake_db.write_bytes(b"db")
    monkeypatch.setattr(expansion, "database_path", lambda: fake_db)
    monkeypatch.setattr(expansion, "_raw_size", lambda: 0)
    config = {
        "targets": {"minimum_securities": 1000},
        "safety": {
            "max_runtime_minutes": 10,
            "max_requests": 100,
            "max_disk_growth_gb": 1,
            "batch_jobs": 10,
            "pages_per_job": 1,
            "checkpoint_securities": 100,
            "request_pause_seconds": 0,
            "max_consecutive_failed_batches": 2,
        },
    }
    result = expansion.run_equity_expansion(con, target=700, config=config)
    assert result["status"] == "target_reached"
    assert result["after"]["securities"] >= 700
    assert result["production_changes"] == 0
    assert con.execute("SELECT count(*) FROM stage30_expansion_checkpoints").fetchone()[0] >= 1


def test_config_declares_finite_resource_limits():
    config = expansion.load_config()
    assert config["safety"]["max_runtime_minutes"] > 0
    assert config["safety"]["max_requests"] > 0
    assert config["safety"]["max_disk_growth_gb"] > 0
    assert Path(expansion.CONFIG_PATH).name == "predictive_data_expansion.yaml"
