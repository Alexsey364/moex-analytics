from moex_analytics.predictive_expansion import orchestrator


def test_orchestrator_sequence_is_research_only(monkeypatch):
    calls = []

    monkeypatch.setattr(orchestrator, "load_config", lambda: {
        "targets": {"minimum_securities": 1000},
        "research": {"refresh_at_securities": 1000, "refresh_at_matured_forecasts": 50},
    })
    monkeypatch.setattr(
        orchestrator,
        "run_equity_expansion",
        lambda *a, **k: calls.append("universe") or {"status": "target_reached"},
    )
    monkeypatch.setattr(orchestrator, "build_market_features", lambda c: calls.append("market") or {})
    monkeypatch.setattr(orchestrator, "deepen_pit_fundamentals", lambda c: calls.append("fundamentals") or {})
    monkeypatch.setattr(
        orchestrator, "build_validated_market_context", lambda c: calls.append("context") or {}
    )
    monkeypatch.setattr(orchestrator, "build_cross_sectional_dataset", lambda c: calls.append("cross") or {})
    monkeypatch.setattr(orchestrator, "measure_data_value", lambda c: calls.append("ablation") or {})

    class Con:
        def execute(self, sql):
            class Result:
                def fetchone(self):
                    return (0,)
            return Result()

    monkeypatch.setattr(orchestrator.market_history, "coverage", lambda *a, **k: {"securities": 1000})
    result = orchestrator.run_predictive_data_expansion(Con())
    assert calls == ["universe", "market", "fundamentals", "context", "cross", "ablation"]
    assert result["automatic_promotion"] == "forbidden"
    assert result["production_changes"] == 0
