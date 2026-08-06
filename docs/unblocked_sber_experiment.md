# Stage 11C: Unblocked SBER Forecast Experiments

This stage is an experimental research layer, not a production trading model. It removes financial-sector history from the mandatory path and evaluates horizon-specific combinations of independently available blocks.

## Modular data tiers

`A` is compact SBER technical data. Optional blocks are `B` ZCYC, `C` market breadth, `D` SBER futures, `E` intraday state, `F` options and `G` fundamentals. Missing optional blocks produce `insufficient_sample` and never block other combinations. Long horizons exclude intraday data; the one-session horizon excludes slow fundamentals.

Features are selected inside each training fold: missing columns are removed, medians and robust scale are learned on training data, near-zero variance and correlations above 0.90 are filtered, and every family has a strict cap. Targets include direction, forward and excess return, MAE/MFE paths, and close/touch thresholds at 3%, 5% and 10%.

## Validation and models

Validation is expanding-window with horizon-sized purge and embargo gaps. The final fold is labelled `reused_holdout_pseudo_oos`; it is not an untouched holdout. Models are deliberately simple: unconditional-frequency and historical-return baselines, regularized logistic classification with Platt calibration, and ridge return estimation. No trees, boosting or neural networks are used.

Diagnostics cover balanced accuracy, ROC-AUC, PR-AUC, Brier score, log loss, expected calibration error, MAE, RMSE, sign accuracy, linear/rank correlation, fold wins, effective sample size and sanity evidence. Numeric probabilities are hidden unless calibration, effective-sample, stability and improvement gates all pass.

## Futures and live boundary

Futures contract scaling and basis remain disabled when official archived fields do not verify multiplier, quotation scale and underlying units. Momentum, volume and open-interest features may still be used with the limitation recorded.

Forecasts, timing comparisons and immutable shadow rows are experimental. They do not feed the production Decision Engine, alter production recommendations or claim a production-ready direction model.

## Commands

Run `python -m moex_analytics.cli run-sber-unblocked-experiment` for the complete idempotent workflow. Separate CLI commands build samples, validate futures specifications, calculate basis, train/calibrate models, evaluate ablations, calculate forecasts, evaluate timing, save shadow forecasts and show status.