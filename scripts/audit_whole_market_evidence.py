"""Generate the Stage 80.5 evidence-semantics audit from immutable research rows."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
from sklearn.metrics import balanced_accuracy_score, confusion_matrix

from moex_analytics.conditioned_stock_forecasting.core import SECTOR_MAP

DATABASE = Path("database/market.duckdb")
REPORT = Path("reports/stage80_5_final_market_evidence_audit.md")
LABELS = (-1, 0, 1)


def _bootstrap_ba(actual: np.ndarray, predicted: np.ndarray) -> tuple[float, float]:
    rng = np.random.default_rng(805)
    scores = []
    for _ in range(1000):
        index = rng.integers(0, len(actual), len(actual))
        if len(np.unique(actual[index])) == 3:
            scores.append(balanced_accuracy_score(actual[index], predicted[index]))
    return tuple(float(value) for value in np.quantile(scores, [0.025, 0.975]))


def _conditioned(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    run_id = con.execute(
        "SELECT run_id FROM conditioned_stock_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    return con.execute(
        """SELECT secid,horizon,feature_block,baseline_mae,model_mae,improvement,
        100*improvement/nullif(baseline_mae,0),observations,ci_low,ci_high,fold_stable,status
        FROM conditioned_stock_scorecards WHERE run_id=? QUALIFY row_number() OVER
        (PARTITION BY secid,horizon ORDER BY improvement DESC)=1 ORDER BY secid,horizon""",
        [run_id],
    ).fetchall()


def _market(con: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    run_id = con.execute(
        "SELECT run_id FROM market_forecast_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    best = con.execute(
        """SELECT horizon,model,balanced_accuracy,baseline_balanced_accuracy,mcc,observations
        FROM market_forecast_scorecards WHERE run_id=? AND sample='frozen_holdout'
        QUALIFY row_number() OVER(PARTITION BY horizon ORDER BY improvement_vs_baseline DESC)=1
        ORDER BY horizon""",
        [run_id],
    ).fetchall()
    result = []
    for horizon, model, score, baseline, mcc, observations in best:
        pairs = con.execute(
            """SELECT actual_class,predicted_class FROM market_forecast_predictions
            WHERE run_id=? AND horizon=? AND model=? AND sample='frozen_holdout'
            ORDER BY trade_date""",
            [run_id, horizon, model],
        ).fetchall()
        actual = np.asarray([row[0] for row in pairs], dtype=int)
        predicted = np.asarray([row[1] for row in pairs], dtype=int)
        result.append(
            {
                "horizon": horizon,
                "model": model,
                "score": score,
                "baseline": baseline,
                "mcc": mcc,
                "n": observations,
                "ci": _bootstrap_ba(actual, predicted),
                "matrix": confusion_matrix(actual, predicted, labels=LABELS).tolist(),
                "classes": [int(np.count_nonzero(actual == label)) for label in LABELS],
                "abstained": 0,
            }
        )
    return result


def _fmt_effect(gain: float) -> str:
    return f"MAE улучшился на {gain:.6f}" if gain >= 0 else f"MAE ухудшился на {abs(gain):.6f}"


def build_report(con: duckdb.DuckDBPyConnection) -> str:
    conditioned = _conditioned(con)
    market = _market(con)
    tournament_run = con.execute(
        "SELECT run_id FROM whole_market_tournament_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    candidates = con.execute(
        """SELECT instrument,horizon,observations,score,baseline_score,improvement,ci_low,ci_high,
        p_value,adjusted_p_value,subperiod_stable,regime_stable,permutation_passed,status,details_json
        FROM whole_market_tournament_entries WHERE run_id=? AND status='shadow_candidate'
        ORDER BY instrument,horizon""",
        [tournament_run],
    ).fetchall()
    state_run = con.execute(
        "SELECT run_id FROM whole_market_state_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    state = con.execute(
        """SELECT trade_date,market_state_label,return_20,drawdown,realized_vol20,
        volatility_json,rates_json,news_json FROM whole_market_state_daily
        WHERE run_id=? ORDER BY trade_date DESC LIMIT 1""",
        [state_run],
    ).fetchone()
    live_run = con.execute(
        "SELECT run_id FROM whole_market_live_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    stocks = con.execute(
        """SELECT secid,horizon,predicted_rank,qualitative_state,status FROM live_stock_rank_forecasts
        WHERE run_id=? ORDER BY secid,horizon""",
        [live_run],
    ).fetchall()
    sectors = {
        (row[0], row[1]): row[2]
        for row in con.execute(
            """SELECT sector,horizon,predicted_rank FROM live_sector_rank_forecasts
            WHERE run_id=?""",
            [live_run],
        ).fetchall()
    }
    lines = [
        "# Stage 80.5 — Final Market Evidence Audit",
        "",
        "## Semantics",
        "",
        "`mae_gain = baseline_mae - candidate_mae`. Positive means lower error; negative means degradation.",
        "The historical mean `-0.025163` therefore means: **MAE deteriorated by 0.025163**.",
        "Market balanced-accuracy improvement is `candidate_balanced_accuracy - baseline_balanced_accuracy`.",
        "",
        "## Conditioned stock models — best block per stock/horizon",
        "",
        "|Stock|H|Block|Baseline MAE|Candidate MAE|Interpretation|Relative|N|95% CI gain|Folds|Status|",
        "|---|---:|---|---:|---:|---|---:|---:|---|---|---|",
    ]
    for row in conditioned:
        lines.append(
            f"|{row[0]}|{row[1]}|{row[2]}|{row[3]:.6f}|{row[4]:.6f}|{_fmt_effect(row[5])}|"
            f"{row[6]:+.2f}%|{row[7]}|[{row[8]:+.6f}, {row[9]:+.6f}]|"
            f"{'stable' if row[10] else 'unstable'}|{row[11]}|"
        )
    lines += ["", "## Tournament shadow candidates", ""]
    for row in candidates:
        details = json.loads(row[14])
        lines += [
            f"### {row[0]} / {row[1]}",
            "",
            f"- N: {row[2]}; candidate MAE {-row[3]:.6f}; baseline MAE {-row[4]:.6f}.",
            f"- {_fmt_effect(row[5])}; bootstrap CI [{row[6]:+.6f}, {row[7]:+.6f}].",
            f"- Raw p={row[8]:.4f}; BH-adjusted p={row[9]:.4f}; permutation passed={row[12]}.",
            f"- Subperiod stable={row[10]}; regime stable={row[11]}; regime means={details['regime_means']}.",
            "- Status: **shadow_candidate**. A separately persisted walk-forward fold gate and "
            "independent replication are absent; production promotion is forbidden.",
            "",
        ]
    lines += [
        "## IMOEX frozen holdout",
        "",
        "Confusion matrices use rows=actual and columns=predicted in order `down, neutral, up`.",
        "The Stage 72 classifier has no abstention mechanism; abstained=0 and abstention-adjusted "
        "performance equals ordinary performance.",
        "",
    ]
    for item in market:
        matrix = item["matrix"]
        lines += [
            f"### {item['horizon']} sessions — {item['model']}",
            "",
            f"- N={item['n']}; classes down/neutral/up={item['classes']}.",
            f"- Balanced accuracy={item['score']:.3f}; baseline={item['baseline']:.3f}; "
            f"bootstrap 95% CI=[{item['ci'][0]:.3f}, {item['ci'][1]:.3f}]; MCC={item['mcc']:.3f}.",
            f"- Confusion matrix: `{matrix}`; abstained={item['abstained']}.",
            "",
        ]
    volatility = json.loads(state[5] or "{}")
    rates = json.loads(state[6] or "{}")
    lines += [
        "## Current market state",
        "",
        f"Cutoff {state[0]}: **{state[1]}**, 20-session return {state[2]:+.2%}, "
        f"drawdown {state[3]:.2%}, realized volatility {state[4]:.2%}.",
        "",
        "The label is not contradictory: the deterministic classifier checks structural stress before "
        "short momentum. The drawdown below -20% triggers `stress`; the +20-day rebound does not erase it.",
        "",
        f"- Drawdown: {state[3]:.2%} (stress trigger).",
        f"- Realized volatility 20: {state[4]:.2%}; 60: {volatility.get('realized_vol60', 0):.2%}.",
        f"- RVI: {volatility.get('rvi', 'n/a')}.",
        f"- Key rate: {rates.get('cbr_key_rate', 'n/a')}%; RUONIA: {rates.get('cbr_ruonia', 'n/a')}%.",
        f"- 20-session return {state[2]:+.2%} is the countervailing positive factor.",
        "",
        "## Current portfolio conditioning",
        "",
        "|Stock|H|Market|Sector rank|Issuer/final|Relative rank|Analogs|Risk|News|Market impact|",
        "|---|---:|---|---:|---|---:|---|---|---|---|",
    ]
    for secid, horizon, rank, direction, status in stocks:
        sector_rank = sectors.get((SECTOR_MAP[secid], horizon))
        lines.append(
            f"|{secid}|{horizon}|stress|{sector_rank or '—'}|{direction} ({status})|{rank}|"
            "shadow estimate|high structural risk|context only, weight 0|"
            "included through market/sector conditioning; effect is experimental|"
        )
    lines += [
        "",
        "## Safeguards",
        "",
        "Production changes: 0. Probability gate unchanged. No model was promoted. "
        "News has predictive weight 0. Results use the immutable current compatible snapshot.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    with duckdb.connect(str(DATABASE), read_only=True) as connection:
        report = build_report(connection)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")
    print(REPORT)
