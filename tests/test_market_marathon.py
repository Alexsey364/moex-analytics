from pathlib import Path

import duckdb

import moex_analytics.market_marathon.core as marathon
from moex_analytics.market_marathon.core import MAX_RUNTIME_SECONDS, render_evidence


def test_marathon_limit_and_windows_wrapper() -> None:
    assert MAX_RUNTIME_SECONDS == 36000
    wrapper = Path("RUN_FULL_MARKET_PREDICTIVE_MARATHON.bat").read_text(encoding="utf-8")
    assert "moex_analytics.market_marathon" in wrapper
    assert "dry" not in wrapper.lower()


def test_evidence_report_discloses_gates_and_negative_results() -> None:
    evidence = {
        "run_id": "x",
        "state": ("2026-01-01", "stress", 0.01, -0.1, 0.2, "{}", "{}", "{}"),
        "market": [(5, "baseline", 0.4, 1 / 3, 0.067, 0.1, 0.2, 0.3, "experimental")],
        "sectors": [(5, 0.04, 0.02, "experimental")],
        "stocks": [("SBERP", 5, "issuer", -0.01, 0.01, "rejected")],
        "lead_lag": [],
        "analog": (1, 0, 1, -0.01, 0.5),
        "live": (5, 40, 45),
    }
    report = render_evidence(evidence)
    assert "Production changes: 0" in report
    assert "Probability gate changed: no" in report
    assert "no production promotion" in report


def test_marathon_is_resumable_and_freezes_input(monkeypatch, tmp_path: Path) -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE canonical_daily_prices(trade_date DATE,close DOUBLE)")
    con.execute("INSERT INTO canonical_daily_prices VALUES ('2026-01-01',100)")
    calls = []

    def completed() -> dict[str, str]:
        calls.append("called")
        return {"status": "completed"}

    for name in (
        "build_whole_market_state",
        "run_market_forecast_research",
        "run_sector_rotation_research",
        "run_conditioned_stock_research",
        "run_lead_lag_research",
        "run_market_analog_fusion",
        "run_whole_market_tournament",
        "create_live_forecasts",
        "evaluate_live_forecasts",
    ):
        monkeypatch.setattr(marathon, name, lambda _con, action=completed: action())
    monkeypatch.setattr(marathon, "_dashboard_snapshot", lambda _con, _run: completed())
    monkeypatch.setattr(marathon, "collect_evidence", lambda _con, run: {"run_id": run})
    monkeypatch.setattr(marathon, "render_evidence", lambda evidence: evidence["run_id"])
    report = tmp_path / "evidence.md"
    first = marathon.run_full_marathon(con, report)
    assert first["production_changes"] == 0
    assert first["probability_gate_changed"] is False
    assert len(calls) == 10
    assert report.read_text(encoding="utf-8") == first["run_id"]
    second = marathon.run_full_marathon(con, report)
    assert second["run_id"] == first["run_id"]
    assert len(calls) == 10
    assert con.execute(
        "SELECT count(*) FROM market_marathon_checkpoints WHERE status='completed'"
    ).fetchone()[0] == 10
