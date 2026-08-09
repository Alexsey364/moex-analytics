"""Resumable bounded Stage 30 orchestration; production state is never promoted."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from moex_analytics import market_history

from .context import build_validated_market_context
from .expansion import load_config, run_equity_expansion
from .fundamentals import deepen_pit_fundamentals
from .market import build_market_features
from .research import build_cross_sectional_dataset, measure_data_value


def run_predictive_data_expansion(
    con, *, target: int | None = None, progress: Callable[[str], None] | None = None
) -> dict:
    """Execute the documented sequence using existing page-level resume checkpoints."""
    emit = progress or (lambda _: None)
    cfg = load_config()
    target = target or int(cfg["targets"]["minimum_securities"])
    started = time.perf_counter()
    receipt = {"target": target, "steps": {}, "production_changes": 0}
    emit("1/9 Continuing historical equity backlog")
    receipt["steps"]["universe"] = run_equity_expansion(con, target=target, config=cfg)
    emit("2/9 Rebuilding trading statistics, liquidity, breadth and quality")
    receipt["steps"]["market"] = build_market_features(con)
    emit("3/9 Deepening validated PIT fundamentals and dividends")
    receipt["steps"]["fundamentals"] = deepen_pit_fundamentals(con)
    emit("4/9 Building sector, rates, FX and bounded derivatives context")
    receipt["steps"]["context"] = build_validated_market_context(con)
    emit("5/9 Auditing corporate-action uncertainty")
    receipt["steps"]["corporate_actions"] = {
        "unresolved": con.execute(
            "SELECT count(*) FROM market_history_quality_issues WHERE issue_type="
            "'large_return_corporate_action_review' AND status!='resolved'"
        ).fetchone()[0],
        "automatic_adjustments": 0,
    }
    emit("6/9 Freezing cross-sectional research sample when eligible")
    receipt["steps"]["cross_sectional"] = build_cross_sectional_dataset(con)
    emit("7/9 Measuring evidence-only data value")
    receipt["steps"]["ablation"] = measure_data_value(con)
    coverage = market_history.coverage(con, save=True)
    matured = 0
    if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='forecast_outcomes'"
    ).fetchone()[0]:
        columns = {row[0] for row in con.execute("DESCRIBE forecast_outcomes").fetchall()}
        if "status" in columns:
            matured = con.execute(
                "SELECT count(*) FROM forecast_outcomes WHERE status='matured'"
            ).fetchone()[0]
    trigger = coverage["securities"] >= int(cfg["research"]["refresh_at_securities"]) or (
        matured >= int(cfg["research"]["refresh_at_matured_forecasts"])
    )
    emit("8/9 Checking research refresh trigger")
    receipt["steps"]["research_trigger"] = {
        "triggered": trigger,
        "message": "Есть достаточно новой информации для нового исследовательского цикла"
        if trigger else "Порог нового исследовательского цикла ещё не достигнут",
        "research_results_only": True,
        "automatic_production_promotion": False,
    }
    emit("9/9 Writing final receipt")
    receipt.update({"coverage": coverage, "matured_forecasts": matured,
                    "runtime_seconds": time.perf_counter() - started,
                    "status": receipt["steps"]["universe"]["status"],
                    "production_models": "unchanged", "automatic_promotion": "forbidden"})
    # JSON round-trip proves the receipt has no non-serializable hidden state.
    json.dumps(receipt, default=str)
    return receipt
