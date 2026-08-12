# Stage 108 — Predictive Return Scientific Evidence

## Stages 101-108

Commits through Stage 107: `06a0670, 8a43133, d46050a, cb94045, c529b52, a70ca34, 3d7c833`. Stage 108 adds the research cockpit and this report.
All layers are research-only. Production Decision Engine and probability gate are unchanged.

## Baseline leaderboard

| secid | horizon | model | mae_pct | sample_size |
| --- | --- | --- | --- | --- |
| GAZP | 1 | drift_3y | 1.419 | 1033 |
| GAZP | 5 | mean_reversion | 3.454 | 1032 |
| GAZP | 20 | no_change | 7.197 | 1029 |
| GAZP | 60 | no_change | 9.697 | 1021 |
| GAZP | 120 | mean_reversion | 11.631 | 1009 |
| GAZP | 250 | no_change | 14.784 | 983 |
| IMOEX | 1 | drift_expanding | 1.012 | 1446 |
| IMOEX | 5 | drift_expanding | 2.491 | 1445 |
| IMOEX | 20 | no_change | 5.181 | 1442 |
| IMOEX | 60 | no_change | 9.814 | 1434 |
| IMOEX | 120 | no_change | 14.989 | 1422 |
| IMOEX | 250 | mean_reversion | 19.29 | 1396 |
| LKOH | 1 | drift_3y | 1.236 | 1166 |
| LKOH | 5 | drift_1y | 2.972 | 1165 |
| LKOH | 20 | market_beta | 6.241 | 1162 |
| LKOH | 60 | no_change | 11.503 | 1154 |
| LKOH | 120 | no_change | 18.634 | 1142 |
| LKOH | 250 | no_change | 33.777 | 1116 |
| LSNG | 1 | drift_3y | 2.025 | 1160 |
| LSNG | 5 | drift_expanding | 5.077 | 1159 |
| LSNG | 20 | drift_expanding | 11.149 | 1156 |
| LSNG | 60 | no_change | 20.881 | 1148 |
| LSNG | 120 | drift_expanding | 30.442 | 1136 |
| LSNG | 250 | no_change | 61.886 | 1110 |
| LSNGP | 1 | market_beta | 1.325 | 1160 |
| LSNGP | 5 | drift_3y | 3.092 | 1160 |
| LSNGP | 20 | drift_3y | 6.975 | 1157 |
| LSNGP | 60 | drift_3y | 13.129 | 1149 |
| LSNGP | 120 | market_beta | 18.826 | 1137 |
| LSNGP | 250 | drift_expanding | 30.577 | 1111 |
| MOEX | 1 | mean_reversion | 1.186 | 691 |
| MOEX | 5 | mean_reversion | 2.834 | 691 |
| MOEX | 20 | mean_reversion | 5.115 | 688 |
| MOEX | 60 | no_change | 8.204 | 680 |
| MOEX | 120 | no_change | 10.992 | 668 |
| MOEX | 250 | no_change | 29.184 | 642 |
| MTSS | 1 | drift_3y | 1.103 | 756 |
| MTSS | 5 | no_change | 2.651 | 755 |
| MTSS | 20 | mean_reversion | 5.754 | 752 |
| MTSS | 60 | mean_reversion | 9.872 | 744 |
| MTSS | 120 | market_beta | 11.516 | 732 |
| MTSS | 250 | mean_reversion | 14.557 | 706 |
| PHOR | 1 | drift_3y | 1.137 | 772 |
| PHOR | 5 | no_change | 2.529 | 771 |
| PHOR | 20 | mean_reversion | 5.017 | 768 |
| PHOR | 60 | mean_reversion | 8.45 | 760 |
| PHOR | 120 | no_change | 9.744 | 748 |
| PHOR | 250 | no_change | 14.238 | 722 |
| SBER | 1 | market_beta | 0.919 | 761 |
| SBER | 5 | market_beta | 2.168 | 760 |
| SBER | 20 | market_beta | 4.128 | 757 |
| SBER | 60 | market_beta | 6.756 | 749 |
| SBER | 120 | market_beta | 8.655 | 737 |
| SBER | 250 | market_beta | 22.582 | 711 |
| SBERP | 1 | no_change | 0.894 | 757 |
| SBERP | 5 | market_beta | 2.135 | 756 |
| SBERP | 20 | market_beta | 4.037 | 753 |
| SBERP | 60 | market_beta | 6.752 | 745 |
| SBERP | 120 | market_beta | 8.483 | 733 |
| SBERP | 250 | drift_expanding | 22.305 | 707 |
| TATN | 1 | drift_expanding | 1.405 | 752 |
| TATN | 5 | market_beta | 3.295 | 751 |
| TATN | 20 | market_beta | 6.47 | 748 |
| TATN | 60 | no_change | 10.236 | 740 |
| TATN | 120 | no_change | 18.364 | 728 |
| TATN | 250 | drift_expanding | 36.9 | 702 |
| TATNP | 1 | no_change | 1.336 | 746 |
| TATNP | 5 | market_beta | 3.14 | 745 |
| TATNP | 20 | market_beta | 6.203 | 742 |
| TATNP | 60 | no_change | 9.575 | 734 |
| TATNP | 120 | no_change | 16.987 | 722 |
| TATNP | 250 | drift_expanding | 38.423 | 696 |
| TRNFP | 1 | drift_3y | 1.272 | 942 |
| TRNFP | 5 | no_change | 3.363 | 941 |
| TRNFP | 20 | market_beta | 7.995 | 938 |
| TRNFP | 60 | market_beta | 17.022 | 930 |
| TRNFP | 120 | market_beta | 30.832 | 918 |
| TRNFP | 250 | mean_reversion | 45.146 | 892 |
| X5 | 1 | drift_3y | 1.289 | 81 |
| X5 | 5 | mean_reversion | 3.455 | 80 |
| X5 | 20 | market_beta | 7.454 | 77 |
| X5 | 60 | drift_1y | 7.317 | 69 |
| X5 | 120 | momentum | 12.893 | 57 |
| X5 | 250 | no_change | 31.129 | 31 |

## Best regularized model by ticker x horizon

| secid | horizon | model | oos_n | mae_pct | baseline_pct | improvement_pct | ci_low_pct | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LKOH | 5 | quantile_q50 | 1164 | 2.981 | 2.97 | -0.37 | -0.042 | FAILED |
| LKOH | 20 | lasso | 1161 | 6.273 | 6.244 | -0.45 | -0.042 | FAILED |
| LKOH | 60 | lasso | 1153 | 12.685 | 11.494 | -10.36 | -1.524 | FAILED |
| LKOH | 120 | lasso | 1141 | 19.289 | 18.618 | -3.6 | -1.25 | FAILED |
| LKOH | 250 | quantile_q50 | 1115 | 34.88 | 33.778 | -3.26 | -2.247 | FAILED |
| LSNGP | 5 | elastic_net | 1159 | 3.079 | 3.092 | 0.43 | -0.004 | WEAK |
| LSNGP | 20 | lasso | 1156 | 6.915 | 6.97 | 0.78 | 0.007 | WEAK |
| LSNGP | 60 | lasso | 1148 | 13.024 | 13.123 | 0.76 | -0.083 | WEAK |
| LSNGP | 120 | lasso | 1136 | 18.797 | 18.829 | 0.17 | -0.489 | WEAK |
| LSNGP | 250 | lasso | 1110 | 29.5 | 30.579 | 3.53 | -0.199 | WEAK |
| MOEX | 5 | quantile_q50 | 690 | 2.881 | 2.834 | -1.67 | -0.091 | FAILED |
| MOEX | 20 | quantile_q50 | 687 | 5.291 | 5.107 | -3.61 | -0.355 | FAILED |
| MOEX | 60 | lasso | 679 | 9.228 | 8.204 | -12.49 | -1.39 | FAILED |
| MOEX | 120 | lasso | 667 | 13.928 | 10.931 | -27.42 | -3.685 | FAILED |
| MOEX | 250 | elastic_net | 641 | 34.72 | 29.069 | -19.44 | -7.067 | FAILED |
| MTSS | 5 | lasso | 754 | 2.658 | 2.654 | -0.16 | -0.022 | FAILED |
| MTSS | 20 | huber | 751 | 5.833 | 5.76 | -1.27 | -0.273 | FAILED |
| MTSS | 60 | huber | 743 | 9.704 | 9.883 | 1.81 | -0.15 | WEAK |
| MTSS | 120 | lasso | 731 | 10.271 | 11.509 | 10.76 | 0.93 | VALIDATED |
| MTSS | 250 | ridge | 705 | 16.129 | 14.549 | -10.86 | -2.463 | FAILED |
| PHOR | 5 | quantile_q50 | 770 | 2.557 | 2.532 | -0.98 | -0.041 | FAILED |
| PHOR | 20 | quantile_q50 | 767 | 5.469 | 5.019 | -8.97 | -0.58 | FAILED |
| PHOR | 60 | lasso | 759 | 10.56 | 8.444 | -25.07 | -2.449 | FAILED |
| PHOR | 120 | ridge | 747 | 12.622 | 9.739 | -29.61 | -3.342 | FAILED |
| PHOR | 250 | quantile_q50 | 721 | 17.323 | 14.222 | -21.8 | -4.274 | FAILED |
| SBERP | 5 | quantile_q50 | 755 | 2.142 | 2.137 | -0.22 | -0.012 | FAILED |
| SBERP | 20 | elastic_net | 752 | 4.095 | 4.039 | -1.4 | -0.091 | FAILED |
| SBERP | 60 | lasso | 744 | 6.862 | 6.759 | -1.52 | -0.191 | FAILED |
| SBERP | 120 | quantile_q50 | 732 | 8.996 | 8.463 | -6.3 | -0.685 | FAILED |
| SBERP | 250 | lasso | 706 | 17.692 | 22.177 | 20.22 | 3.129 | VALIDATED |
| TATNP | 5 | quantile_q50 | 744 | 3.149 | 3.142 | -0.22 | -0.02 | FAILED |
| TATNP | 20 | lasso | 741 | 6.323 | 6.206 | -1.87 | -0.192 | FAILED |
| TATNP | 60 | ridge | 733 | 9.789 | 9.551 | -2.5 | -0.68 | FAILED |
| TATNP | 120 | ridge | 721 | 17.429 | 16.906 | -3.09 | -1.512 | FAILED |
| TATNP | 250 | quantile_q50 | 695 | 37.177 | 38.351 | 3.06 | 1.077 | WEAK |
| TRNFP | 5 | lasso | 940 | 3.365 | 3.365 | 0.0 | -0.018 | WEAK |
| TRNFP | 20 | quantile_q50 | 937 | 8.022 | 7.998 | -0.3 | -0.056 | FAILED |
| TRNFP | 60 | quantile_q50 | 929 | 17.07 | 17.03 | -0.23 | -0.085 | FAILED |
| TRNFP | 120 | quantile_q50 | 917 | 30.478 | 30.849 | 1.2 | 0.288 | WEAK |
| TRNFP | 250 | quantile_q50 | 891 | 75.162 | 45.175 | -66.38 | -33.177 | FAILED |
| X5 | 5 | ridge | 79 | 3.32 | 3.478 | 4.55 | -0.143 | WEAK |
| X5 | 20 | quantile_q50 | 76 | 7.724 | 7.549 | -2.32 | -0.26 | FAILED |
| X5 | 60 | quantile_q50 | 68 | 7.461 | 7.333 | -1.75 | -0.61 | FAILED |

`VALIDATED` requires OOS N, ≥2% improvement against the actual baseline champion, positive
bootstrap CI, subperiod stability and coefficient-sign stability. No automatic promotion occurred.

## Cross-sectional ranking

| horizon | model | observations | dates | rank_ic | ci_low | ci_high | top_quintile_pct | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | elasticnet_proxy | 10406 | 844 | 0.05 | 0.024 | 0.075 | 0.01 | SHADOW_CANDIDATE |
| 5 | linear_ranking | 10354 | 840 | 0.089 | 0.069 | 0.108 | 0.08 | SHADOW_CANDIDATE |
| 20 | linear_ranking | 10159 | 825 | 0.098 | 0.076 | 0.12 | 0.07 | SHADOW_CANDIDATE |
| 60 | linear_ranking | 9639 | 785 | 0.088 | 0.064 | 0.112 | 0.79 | SHADOW_CANDIDATE |
| 120 | linear_ranking | 8859 | 725 | 0.12 | 0.095 | 0.145 | 1.89 | SHADOW_CANDIDATE |
| 250 | linear_ranking | 7169 | 595 | 0.142 | 0.112 | 0.168 | 3.69 | SHADOW_CANDIDATE |

## Fundamental expected return

Dividend components are available for several securities. Unit-validated earnings growth and
valuation re-rating are not available, therefore all fundamental total-return/fair-value outputs
remain `INSUFFICIENT_DATA`; fundamental ranges are not predictive intervals.

## Macro sensitivity

91 explanatory exposures were estimated. No macro factor has proven OOS predictive usefulness,
so macro contribution to the ensemble is zero.

## SBERP today

| horizon | expected_pct | disagreement_pct | confidence | status | best_model |
| --- | --- | --- | --- | --- | --- |
| 5 | 0.18 | 0.0 | 0.3 | NO_PROVEN_FORECAST_EDGE | market_beta |
| 20 | 0.73 | 0.0 | 0.3 | NO_PROVEN_FORECAST_EDGE | market_beta |
| 60 | 2.48 | 0.0 | 0.3 | NO_PROVEN_FORECAST_EDGE | market_beta |
| 120 | 7.17 | 0.0 | 0.3 | NO_PROVEN_FORECAST_EDGE | market_beta |
| 250 | 6.29 | 11.57 | 0.39 | ENSEMBLE_VALIDATED | drift_expanding |

Current price/cutoff are read in the cockpit. Numeric P(up) is not published. Analog evidence
remains historical stress/path context only. SBERP 5/20/60/120 have no proven forecast edge;
250d has validated statistical variants but material component disagreement.

## Did we beat the baseline?

Only MTSS/120 and SBERP/250 have regularized variants passing every current research gate.
Most ticker x horizon combinations did not beat their baseline champion. Ranking evidence is
broader and more stable than absolute-return evidence, especially at 120/250 sessions.

## Scientific verdict

**Cross-sectional edge exists; absolute-return edge is sparse and horizon-specific.** Fundamental long-horizon evidence is currently limited by unit-safe PIT inputs;
macro exposures are explanatory but not predictive. The correct state for most absolute-return
horizons is `NO PROVEN FORECAST EDGE` or `BASELINE REMAINS BEST`.
