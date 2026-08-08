# Automated Daily Evaluation and Model Governance

The default one-click workflow is `quick-daily-update`. It checks eight bounded steps and uses a five-day
revision window where an external request is needed. An unchanged logical market cutoff produces
`no_change`: dependent market calculations are skipped, forecast IDs are not duplicated, and historical
backfills or model tuning are never started.

Update levels are deliberately separate:

- **quick**: incremental data checks, portfolio state, immutable forecast capture and matured evaluation;
- **deep**: issuer/document discovery, audits and larger data checks;
- **retrain**: Alpha Research, nested CV and challenger comparison only.

Deep/retrain operations are explicit. The launcher invokes only quick mode. Failures are isolated per source
and logged as `failed_using_previous_snapshot`; the dashboard must disclose that the previous validated data
is being used.

Models in `model_registry` are frozen. Daily updates cannot change features, coefficients, thresholds or
calibration. Challengers begin in `shadow`. Promotion is only a recommendation and requires sufficient live
sample, regime stability, baseline and production outperformance, calibration, leakage checks, absence of a
structural break, and explicit human approval. No code path automatically promotes a model.

Data drift (PSI), concept drift (relationship change), and live degradation are separate statuses. A retrain
signal is only a suggestion to conduct research; it never starts heavy retraining automatically.
