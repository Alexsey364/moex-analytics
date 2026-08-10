# Strict Historical Analog Validation

Stage 49 evaluates immutable Stage 47 OOS predictions with chronological validation and untouched latest holdout splits. There is no shuffle. Each prediction retains a `train_end` before its replay cutoff. Model weights were fixed before holdout evaluation.

Metrics include balanced/sign accuracy, MAE, RMSE, Spearman/Rank IC and abstention. Interval coverage remains unavailable where the replay did not persist interval forecasts; it is not reconstructed. MAE differences against existing evidence use a deterministic contiguous block bootstrap whose block length is at least the forecast horizon.

Statuses are evidence labels only: `NO_EVIDENCE`, `WEAK_EVIDENCE`, `ANALOG_USEFUL`, `FUSION_IMPROVED`, or `SHADOW_CANDIDATE`. Combinations without a sufficient validation sample are explicit `not_evaluated`; the holdout is never used to create them or tune K. Production and probability policy remain unchanged.

## Frozen-policy run

Run `152ae5700096fb891759` evaluated 105,689 strict analog predictions, 280 per-instrument/horizon/method selections, 2,940 scorecards and 2,160 block-bootstrap comparisons. State-only, path-only, state+path, state+regime, state+event, state+issuer and full analog methods were searched with K=5/10/20/30/50 on validation. Each method then used one selected K, a frozen scaler/regime/similarity policy and an outcome library ending at `validation_end` throughout holdout.

Executable audits found zero holdout-updated selections, multi-K holdout cells, library/policy hash mismatches, probability-enabled predictions and `train_end >= test_start` violations. Eight instruments and all five requested horizons were evaluable; X5 remained `insufficient_data` because its canonical history did not meet the frozen sample boundary.

Most results are `NO_EVIDENCE` or `WEAK_EVIDENCE`. The aggregate frozen fusion point improvement is small (roughly 0.0025–0.0029 MAE) and direction accuracy remains about 0.49. Statistically supported research-only cells are limited to LSNGP 20-session performance weighting, LSNGP 60-session analog/fusion variants, and PHOR 20-session regime/event fusion. They remain shadow candidates; production changes are zero.

The invalid v1 validation run `cb1bdd919e3a031f62c9` remains available for audit with status `invalid_temporal_leakage` and is excluded from completed-run status and all valid evidence summaries.
