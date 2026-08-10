# Multi-Model Predictive Fusion

Stage 47 is a research/shadow layer. It keeps baseline, technical, pooled, regime, sector, macro, fundamental, valuation, analog, event-conditioned analog and meta-confidence evidence visible rather than collapsing them into an unexplained score.

The OOS study compares existing evidence, existing plus analog, analog plus regime, analog plus regime and events, static equal weights and weights estimated from prior OOS errors. At every replay cutoff, weighting history ends before that cutoff. The latest 20% of each instrument/horizon timeline is an untouched evaluation holdout and is never used to tune weights.

Weak analog sample, novel regime/event context, stale inputs or strong disagreement trigger explicit abstention. Every stored result is `shadow_only`; `probability_allowed` remains false. No production model or Decision Engine object is read for mutation or updated.

## Reproduced run

The deterministic run `e0101d520122fab3a548` stored 151,530 OOS variant predictions, 277,805 visible evidence-block records and 45 current research cells. The latest 5,065 observations per variant are flagged as untouched holdout. Temporal-boundary violations and probability-enabled records are both zero. A second run reproduced the same identifiers and counts.

The initial aggregate evidence does **not** support promotion: existing pooled evidence had lower MAE (0.0569) than existing-plus-analog (0.0907), analog-plus-regime (0.0924), and analog-plus-regime-plus-events (0.1045). OOS-performance weighting reached MAE 0.0607 but still did not beat the existing evidence. Forty current cells have analog data and five are insufficient; all 45 abstain because the current event context lacks a sufficient historical match. These aggregate figures precede Stage 49's strict per-instrument holdout inference and are not production claims.
