# Multi-Model Predictive Fusion

Stage 47 is a research/shadow layer. It keeps baseline, technical, pooled, regime, sector, macro, fundamental, valuation, analog, event-conditioned analog and meta-confidence evidence visible rather than collapsing them into an unexplained score.

The OOS study now separates `pseudo_oos_adaptive`, `untouched_holdout_frozen`, and `live_shadow`. Train (60%), validation (20%) and holdout (20%) boundaries are fixed before fitting. Model selection, component weights, analog K, similarity policy, abstention threshold, calibration version, regime/event policies and scaler/PCA versions are serialized in an immutable `fusion_policy_snapshot` at `validation_end`. Every prediction in a cell's holdout carries the same policy hash. Holdout outcomes never update that policy.

Weak analog sample, novel regime/event context, stale inputs or strong disagreement trigger explicit abstention. Every stored result is `shadow_only`; `probability_allowed` remains false. No production model or Decision Engine object is read for mutation or updated.

## Invalid v1 audit record

Run `e0101d520122fab3a548` remains immutable with status `invalid_temporal_leakage`. It updated performance weights using outcomes revealed earlier inside the alleged untouched holdout and used a prior realized outcome as a missing-pooled fallback. Its apparent weighted holdout direction accuracy was 0.771 with MAE 0.0604. These numbers are excluded from all evidence, leaderboard and promotion paths.

## Frozen-policy v2 run

Run `e2c5a8029b15382d1e10` created 40 immutable policy snapshots, 30,390 frozen-holdout predictions and 151,530 separately labelled adaptive pseudo-OOS predictions. Hash mismatches, multiple hashes within a holdout cell, predictions before `holdout_start`, probability-enabled records and non-shadow records are all zero.

The valid aggregate frozen holdout does **not** reproduce the invalid edge. Direction accuracy is approximately 0.50 across variants. Existing evidence has MAE 0.1415; existing-plus-analog 0.1387; analog/regime/events 0.1387; performance-weighted 0.1391. These small point improvements require Stage 49 bootstrap validation and do not support promotion by themselves.
