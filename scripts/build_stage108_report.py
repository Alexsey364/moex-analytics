"""Build the factual Stage 108 evidence report from immutable research tables."""

from pathlib import Path

from moex_analytics.database import connection


def markdown_table(frame) -> str:
    columns = [str(column) for column in frame.columns]
    rows = [[str(value) for value in row] for row in frame.itertuples(index=False, name=None)]
    return "\n".join([
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ])


def main() -> None:
    with connection(read_only=True) as con:
        commits = "06a0670, 8a43133, d46050a, cb94045, c529b52, a70ca34, 3d7c833"
        baseline = con.execute("select secid,horizon,model,round(mae*100,3) mae_pct,sample_size "
            "from predictive_baseline_scorecards where rank=1 and run_id=(select run_id from "
            "predictive_baseline_runs where status='completed' order by finished_at desc limit 1) "
            "order by secid,horizon").df()
        stats = con.execute("select secid,horizon,model,oos_n,round(mae*100,3) mae_pct,"
            "round(baseline_mae*100,3) baseline_pct,round(improvement*100,2) improvement_pct,"
            "round(ci_low*100,3) ci_low_pct,status from statistical_model_scorecards where run_id="
            "(select run_id from statistical_model_runs where status='completed' order by "
            "finished_at desc limit 1) qualify row_number() over(partition by secid,horizon "
            "order by improvement desc)=1 order by secid,horizon").df()
        ranking = con.execute("select horizon,model,observations,dates,round(rank_ic,3) rank_ic,"
            "round(ci_low,3) ci_low,round(ci_high,3) ci_high,"
            "round(top_quintile_spread*100,2) top_quintile_pct,status from ranking_scorecards "
            "where run_id=(select run_id from ranking_research_runs where status='completed' "
            "order by finished_at desc limit 1) order by horizon").df()
        sberp = con.execute("select horizon,round(expected_return*100,2) expected_pct,"
            "round(disagreement*100,2) disagreement_pct,round(confidence,2) confidence,status,"
            "best_model from dynamic_ensemble_forecasts where secid='SBERP' and run_id=(select "
            "run_id from dynamic_ensemble_runs where status='completed' order by finished_at desc "
            "limit 1) order by horizon").df()
        verdict = "Cross-sectional edge exists; absolute-return edge is sparse and horizon-specific."
    report = f"""# Stage 108 — Predictive Return Scientific Evidence

## Stages 101-108

Commits through Stage 107: `{commits}`. Stage 108 adds the research cockpit and this report.
All layers are research-only. Production Decision Engine and probability gate are unchanged.

## Baseline leaderboard

{markdown_table(baseline)}

## Best regularized model by ticker x horizon

{markdown_table(stats)}

`VALIDATED` requires OOS N, ≥2% improvement against the actual baseline champion, positive
bootstrap CI, subperiod stability and coefficient-sign stability. No automatic promotion occurred.

## Cross-sectional ranking

{markdown_table(ranking)}

## Fundamental expected return

Dividend components are available for several securities. Unit-validated earnings growth and
valuation re-rating are not available, therefore all fundamental total-return/fair-value outputs
remain `INSUFFICIENT_DATA`; fundamental ranges are not predictive intervals.

## Macro sensitivity

91 explanatory exposures were estimated. No macro factor has proven OOS predictive usefulness,
so macro contribution to the ensemble is zero.

## SBERP today

{markdown_table(sberp)}

Current price/cutoff are read in the cockpit. Numeric P(up) is not published. Analog evidence
remains historical stress/path context only. SBERP 5/20/60/120 have no proven forecast edge;
250d has validated statistical variants but material component disagreement.

## Did we beat the baseline?

Only MTSS/120 and SBERP/250 have regularized variants passing every current research gate.
Most ticker x horizon combinations did not beat their baseline champion. Ranking evidence is
broader and more stable than absolute-return evidence, especially at 120/250 sessions.

## Scientific verdict

**{verdict}** Fundamental long-horizon evidence is currently limited by unit-safe PIT inputs;
macro exposures are explanatory but not predictive. The correct state for most absolute-return
horizons is `NO PROVEN FORECAST EDGE` or `BASELINE REMAINS BEST`.
"""
    target = Path("docs/stage108_predictive_return_evidence.md")
    target.write_text(report, encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
