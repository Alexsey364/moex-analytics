"""Checkpointed Stage 60 orchestration and factual evidence reporting."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from moex_analytics.config import PROJECT_ROOT
from moex_analytics.distribution_engine.core import run_distribution_research
from moex_analytics.multi_horizon_engine.core import run_multi_horizon_research
from moex_analytics.opportunity_engine.core import run_opportunity_research
from moex_analytics.portfolio_optimizer.core import run_portfolio_optimizer
from moex_analytics.predictive_targets.core import build_predictive_targets
from moex_analytics.ranking_engine.core import run_ranking_research
from moex_analytics.scenario_engine.core import run_scenario_research
from moex_analytics.timing_engine.core import run_timing_research

VERSION = "predictive-research-marathon-v3-full-evidence"
PORTFOLIO = {"X5", "SBERP", "LKOH", "LSNGP", "MTSS", "TRNFP", "TATNP", "PHOR", "MOEX"}
STEPS = (
    "targets", "ranking", "distribution", "scenario", "timing", "opportunity",
    "portfolio", "validation", "current", "report",
)
DDL = """
CREATE TABLE IF NOT EXISTS predictive_marathon_runs(
 run_id VARCHAR PRIMARY KEY,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 current_step VARCHAR,completed_steps_json JSON,runtime_seconds DOUBLE,max_runtime_hours DOUBLE,
 report_path VARCHAR,version VARCHAR,details_json JSON);
CREATE TABLE IF NOT EXISTS predictive_marathon_checkpoints(
 run_id VARCHAR,step VARCHAR,started_at TIMESTAMP,finished_at TIMESTAMP,status VARCHAR,
 result_json JSON,runtime_seconds DOUBLE,PRIMARY KEY(run_id,step));
"""


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def _checkpoint(con: Any, run_id: str, step: str, action: Callable[[], dict]) -> dict:
    old = con.execute(
        "SELECT status,result_json FROM predictive_marathon_checkpoints WHERE run_id=? AND step=?",
        [run_id, step],
    ).fetchone()
    if old and old[0] == "completed":
        return json.loads(old[1])
    con.execute(
        "INSERT OR REPLACE INTO predictive_marathon_checkpoints "
        "(run_id,step,started_at,status) VALUES (?,?,current_timestamp,'running')",
        [run_id, step],
    )
    con.execute("UPDATE predictive_marathon_runs SET current_step=? WHERE run_id=?", [step, run_id])
    started = time.perf_counter()
    print(f"[{datetime.now(UTC):%H:%M:%S}] START {step}", flush=True)
    try:
        result = action()
        elapsed = time.perf_counter() - started
        con.execute(
            "UPDATE predictive_marathon_checkpoints SET finished_at=current_timestamp,"
            "status='completed',result_json=?,runtime_seconds=? WHERE run_id=? AND step=?",
            [json.dumps(result, default=str), elapsed, run_id, step],
        )
        print(f"[{datetime.now(UTC):%H:%M:%S}] DONE  {step} ({elapsed:.1f}s)", flush=True)
        return result
    except Exception as exc:
        con.execute(
            "UPDATE predictive_marathon_checkpoints SET finished_at=current_timestamp,"
            "status='failed',result_json=? WHERE run_id=? AND step=?",
            [json.dumps({"type": type(exc).__name__, "error": str(exc)}), run_id, step],
        )
        raise


def _current_summary(con: Any) -> dict:
    rows = con.execute(
        "SELECT secid,count(*),count(*) FILTER (WHERE abstain) FROM opportunity_candidates "
        "WHERE run_id=(SELECT run_id FROM opportunity_research_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1) AND candidate_type='equity' GROUP BY secid ORDER BY secid"
    ).fetchall()
    winners = con.execute(
        "SELECT tranche,status,allocation_json,cash_reserve FROM portfolio_allocation_plans "
        "WHERE run_id=(SELECT run_id FROM cash_aware_optimizer_runs WHERE status='completed' "
        "ORDER BY finished_at DESC LIMIT 1) AND plan_rank=1 ORDER BY tranche"
    ).fetchall()
    return {"stocks": rows, "allocation_winners": winners, "production_changes": 0}


def _validated_targets(con: Any) -> dict:
    """Quarantine an incomplete universe and reuse the newest complete immutable target run."""
    source_rows = con.execute(
        "SELECT canonical_secid,max(trade_date) FROM daily_returns "
        "WHERE canonical_secid<>'IMOEX' GROUP BY canonical_secid"
    ).fetchall()
    source_securities = {row[0] for row in source_rows}
    source_cutoff = max((row[1] for row in source_rows), default=None)
    stale = {
        secid for secid, last_date in source_rows
        if secid in PORTFOLIO and source_cutoff and (source_cutoff - last_date).days > 10
    }
    source_missing = sorted((PORTFOLIO - source_securities) | stale)
    if source_missing:
        latest = con.execute(
            "SELECT run_id FROM predictive_target_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if not latest:
            raise ValueError(f"incomplete daily_returns universe; missing: {source_missing}")
        result = {"run_id": latest[0]}
    else:
        result = build_predictive_targets(con)
    target_run = result["run_id"]
    securities = {row[0] for row in con.execute(
        "SELECT DISTINCT secid FROM predictive_target_observations WHERE run_id=? AND secid<>'IMOEX'",
        [target_run],
    ).fetchall()}
    missing = sorted((PORTFOLIO - securities) | set(source_missing))
    if not missing:
        return result
    con.execute(
        "UPDATE predictive_target_runs SET status='invalid_incomplete_universe' WHERE run_id=?",
        [target_run],
    )
    ranking_runs = [row[0] for row in con.execute(
        "SELECT run_id FROM ranking_research_runs WHERE target_run_id=?", [target_run]
    ).fetchall()]
    for ranking_run in ranking_runs:
        con.execute(
            "UPDATE ranking_research_runs SET status='invalid_incomplete_universe' WHERE run_id=?",
            [ranking_run],
        )
        con.execute(
            "UPDATE distribution_research_runs SET status='invalid_incomplete_universe' "
            "WHERE ranking_run_id=?", [ranking_run],
        )
        con.execute(
            "UPDATE timing_research_runs SET status='invalid_incomplete_universe' "
            "WHERE ranking_run_id=?", [ranking_run],
        )
    invalid_dependencies = con.execute(
        "SELECT run_id FROM opportunity_research_runs WHERE ranking_run_id IN "
        "(SELECT run_id FROM ranking_research_runs WHERE status='invalid_incomplete_universe')"
    ).fetchall()
    for (opportunity_run,) in invalid_dependencies:
        con.execute(
            "UPDATE opportunity_research_runs SET status='invalid_incomplete_universe' WHERE run_id=?",
            [opportunity_run],
        )
        con.execute(
            "UPDATE cash_aware_optimizer_runs SET status='invalid_incomplete_universe' "
            "WHERE opportunity_run_id=?", [opportunity_run],
        )
        con.execute(
            "UPDATE multi_horizon_runs SET status='invalid_incomplete_universe' "
            "WHERE opportunity_run_id=?", [opportunity_run],
        )
    fallback = con.execute(
        "SELECT r.run_id,r.observation_rows,r.entry_rows,r.cutoff FROM predictive_target_runs r "
        "WHERE r.status='completed' AND (SELECT count(DISTINCT o.secid) FROM "
        "predictive_target_observations o WHERE o.run_id=r.run_id AND o.secid IN "
        "('X5','SBERP','LKOH','LSNGP','MTSS','TRNFP','TATNP','PHOR','MOEX'))=9 "
        "ORDER BY r.finished_at DESC LIMIT 1"
    ).fetchone()
    if not fallback:
        raise ValueError(f"incomplete portfolio target universe; missing: {missing}")
    return {"run_id": fallback[0], "status": "completed", "observations": fallback[1],
            "entry_targets": fallback[2], "cutoff": fallback[3], "fallback": True,
            "quarantined_run": target_run, "missing": missing}


def build_report(con: Any, run_id: str) -> str:
    rank = con.execute(
        "SELECT horizon,model,observations,dates,rank_ic,ci_low,ci_high,top3_excess,status "
        "FROM ranking_scorecards WHERE sample_type='untouched_holdout_frozen' "
        "QUALIFY row_number() OVER "
        "(PARTITION BY horizon ORDER BY rank_ic DESC NULLS LAST)=1 ORDER BY horizon"
    ).fetchall()
    distribution = con.execute(
        "SELECT horizon,method,observations,median_mae,coverage_80,baseline_delta,status "
        "FROM distribution_scorecards WHERE sample_type='untouched_holdout_frozen' "
        "QUALIFY row_number() OVER "
        "(PARTITION BY horizon ORDER BY median_mae)=1 ORDER BY horizon"
    ).fetchall()
    timing = con.execute(
        "SELECT horizon,policy,cases,mean_return,mean_max_drawdown,status "
        "FROM timing_policy_scorecards WHERE sample_type='untouched_holdout_frozen' "
        "AND context='all' "
        "QUALIFY row_number() OVER (PARTITION BY horizon ORDER BY mean_return DESC)=1 "
        "ORDER BY horizon"
    ).fetchall()
    current = _current_summary(con)
    current_detail = con.execute(
        "SELECT secid,relative_rank,expected_median,tail_downside,timing_status,evidence_quality "
        "FROM opportunity_candidates WHERE run_id=(SELECT run_id FROM opportunity_research_runs "
        "WHERE status='completed' ORDER BY finished_at DESC LIMIT 1) AND candidate_type='equity' "
        "AND horizon=60 ORDER BY relative_rank DESC"
    ).fetchall()
    analogs = con.execute(
        "SELECT secid,analog_date,round(similarity_score*100,1) FROM historical_analogs_v3 "
        "WHERE analog_type='issuer' AND method='robust_euclidean' AND episode_rank=1 "
        "QUALIFY row_number() OVER (PARTITION BY secid ORDER BY cutoff DESC)=1 ORDER BY secid"
    ).fetchall()
    portfolio_backtest = con.execute(
        "SELECT method,periods,terminal_wealth,max_drawdown,ex_post_regret "
        "FROM portfolio_optimizer_backtests WHERE run_id=(SELECT run_id FROM "
        "cash_aware_optimizer_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1) "
        "ORDER BY method"
    ).fetchall()
    ablation = con.execute(
        "SELECT gate_status,count(*) FROM horizon_feature_ablation WHERE run_id=(SELECT run_id "
        "FROM multi_horizon_runs WHERE status='completed' ORDER BY finished_at DESC LIMIT 1) "
        "GROUP BY gate_status ORDER BY gate_status"
    ).fetchall()
    live = con.execute(
        "SELECT count(*),count(*) FILTER(WHERE coalesce(o.outcome_status,'pending')='pending'),"
        "count(*) FILTER(WHERE o.outcome_status='matured') FROM forecast_registry r "
        "LEFT JOIN forecast_outcomes o USING(forecast_id)"
    ).fetchone()
    lines = [
        "# Predictive Research Marathon — Evidence Report", "",
        f"Generated: {datetime.now(UTC).isoformat()}", f"Run: `{run_id}`", "",
        "> Research/shadow only. Production Decision Engine and probability gate unchanged.", "",
        "## Frozen methodology", "",
        "Temporal splits, target exit dates, model policies and availability boundaries are frozen. "
        "Selection is validation-only; holdout is diagnostic. Overlapping labels are purged or "
        "reported with date-level effective sample size. No synthetic history is used.", "",
        "## Ranking holdout", "",
        "| Horizon | Model | Obs | Dates | Rank IC | 95% CI | Top-3 excess | Status |",
        "|---:|---|---:|---:|---:|---|---:|---|",
    ]
    lines += [
        f"| {h} | {m} | {n} | {d} | {ic:.4f} | [{lo:.4f}, {hi:.4f}] | {top:.4f} | {s} |"
        for h, m, n, d, ic, lo, hi, top, s in rank
    ]
    lines += ["", "Multiple model/horizon comparisons remain research-only; no status is promoted "
              "from a point estimate alone.", "", "## Distribution holdout", "",
              "| Horizon | Method | Obs | Median MAE | Coverage 80 | Δ baseline | Status |",
              "|---:|---|---:|---:|---:|---:|---|"]
    lines += [f"| {h} | {m} | {n} | {mae:.4f} | {cov:.3f} | {delta:.4f} | {s} |"
              for h, m, n, mae, cov, delta, s in distribution]
    lines += ["", "## Timing experiment", "",
              "| Horizon | Policy | Obs | Mean return | Max drawdown | Status |",
              "|---:|---|---:|---:|---:|---|"]
    lines += [f"| {h} | {p} | {n} | {ret:.4f} | {dd:.4f} | {s} |"
              for h, p, n, ret, dd, s in timing]
    lines += ["", "## Current nine-stock research", "",
              "| Secid | Rank | Median | Tail | Timing | Evidence |",
              "|---|---:|---:|---:|---|---|"]
    lines += [f"| {secid} | {rank:.3f} | {median:.4f} | {tail:.4f} | {timing} | {evidence} |"
              for secid, rank, median, tail, timing, evidence in current_detail]
    lines += ["", "Top current robust-Euclidean analogs: " + "; ".join(
        f"{secid} — {date} ({score}/100)" for secid, date, score in analogs
    ) + "."]
    lines += ["", "## Cash-aware additions", "",
              "| Tranche | Winner | Cash reserve |", "|---:|---|---:|"]
    lines += [f"| {tranche:.0f} | {status} `{allocation}` | {reserve:.0f} |"
              for tranche, status, allocation, reserve in current["allocation_winners"]]
    lines += ["", "## Historical portfolio comparison", "",
              "| Method | Independent periods | Terminal wealth | Max drawdown | Ex-post regret |",
              "|---|---:|---:|---:|---:|"]
    lines += [f"| {method} | {periods} | {wealth:.4f} | {drawdown:.4f} | {regret:.4f} |"
              for method, periods, wealth, drawdown, regret in portfolio_backtest]
    lines += ["", "## Ablation and multiplicity", "",
              "Feature-family gate outcomes: " + ", ".join(
                  f"{status} {count}" for status, count in ablation
              ) + ".",
              "These gates are horizon-specific and validation-selected; holdout diagnostics and "
              "confidence intervals are not converted into automatic promotion after multiple tests.",
              "", "## Live separation and Stage 50 comparison", "",
              f"Forecast registry: total {live[0]}, pending {live[1]}, matured {live[2]}.",
              "Stage 50 found localized analog value but no general robust analog advantage. "
              "Stages 52-60 add cross-sectional ranking, distributions, scenarios, timing and "
              "portfolio allocation; insufficient evidence still produces abstention or CASH.", "",
              "Automatic promotion: **0**. Production changes: **0**. Probability-gate changes: **0**."]
    return "\n".join(lines)


def run_predictive_research_marathon(con: Any, max_runtime_hours: float = 10.0) -> dict[str, Any]:
    ensure_schema(con)
    cutoff = con.execute("SELECT max(trade_date) FROM canonical_daily_prices").fetchone()[0]
    run_id = hashlib.sha256(f"{VERSION}|{cutoff}".encode()).hexdigest()[:20]
    existing = con.execute(
        "SELECT status,report_path,runtime_seconds FROM predictive_marathon_runs WHERE run_id=?",
        [run_id],
    ).fetchone()
    if existing and existing[0] == "completed":
        return {"run_id": run_id, "status": "completed", "report": existing[1],
                "runtime_seconds": existing[2], "cached": True}
    if not existing:
        con.execute(
            "INSERT INTO predictive_marathon_runs "
            "(run_id,started_at,status,completed_steps_json,max_runtime_hours,version,details_json) "
            "VALUES (?,current_timestamp,'running','[]',?,?,?)",
            [run_id, max_runtime_hours, VERSION, json.dumps({"production_changes": 0})],
        )
    started = time.perf_counter()
    actions = {
        "targets": lambda: _validated_targets(con),
        "ranking": lambda: run_ranking_research(con),
        "distribution": lambda: run_distribution_research(con),
        "scenario": lambda: run_scenario_research(con),
        "timing": lambda: run_timing_research(con),
        "opportunity": lambda: run_opportunity_research(con),
        "portfolio": lambda: run_portfolio_optimizer(con),
        "validation": lambda: run_multi_horizon_research(con),
        "current": lambda: _current_summary(con),
    }
    completed = []
    try:
        for step in STEPS[:-1]:
            if (time.perf_counter() - started) / 3600 > max_runtime_hours:
                raise TimeoutError("configured marathon runtime reached; rerun to resume")
            _checkpoint(con, run_id, step, actions[step])
            completed.append(step)
        path = PROJECT_ROOT / "reports" / "predictive_research_marathon.md"

        def write_report() -> dict:
            text = build_report(con, run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
            return {"path": str(path), "characters": len(text)}

        _checkpoint(con, run_id, "report", write_report)
        completed.append("report")
        elapsed = time.perf_counter() - started
        con.execute(
            "UPDATE predictive_marathon_runs SET finished_at=current_timestamp,status='completed',"
            "current_step=NULL,completed_steps_json=?,runtime_seconds=?,report_path=?,details_json=? "
            "WHERE run_id=?",
            [json.dumps(completed), elapsed, str(path),
             json.dumps({"production_changes": 0, "probability_gate_changes": 0}), run_id],
        )
        return {"run_id": run_id, "status": "completed", "steps": completed,
                "runtime_seconds": elapsed, "report": str(path), "cached": False}
    except Exception as exc:
        con.execute(
            "UPDATE predictive_marathon_runs SET status='failed',details_json=? WHERE run_id=?",
            [json.dumps({"error": str(exc), "completed": completed}), run_id],
        )
        raise


def marathon_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute(
        "SELECT run_id,status,current_step,runtime_seconds,report_path FROM predictive_marathon_runs "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": None} if not row else dict(zip(
        ("run_id", "status", "current_step", "runtime_seconds", "report"), row, strict=True
    ))
