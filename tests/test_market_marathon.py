from pathlib import Path

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
