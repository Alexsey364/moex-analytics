# Stage 14 — Portfolio validation and methodology adoption

Date: 2026-08-07. Version: `portfolio-validation-v1`. All outputs are research-only. The SBER Production Decision Engine is unchanged and no BUY/SELL status is produced.

## Alpha protocol

Stage-13 screening is preserved separately. Validation uses expanding walk-forward folds, horizon purge/embargo, train-only standardization and regression, fold-level OOS predictions, horizon-aware block bootstrap, autocorrelation-adjusted effective sample size, Newey–West diagnostic, coefficient sign stability, stress/normal regime slices, label permutation and random-noise tests. Every bootstrap draw is stored; no proxy CI is used.

Status requires improvement over the unconditional train baseline. A positive OOS IC alone is insufficient. The current run produced no `validated_candidate`: LKOH is conditional, TATNP rejected, and the remaining candidates unstable or rejected after the final CI rule. These labels cannot promote a production model.

Cross-factor validation is leave-one-instrument-out. `volatility_60` was positive on every held-out series in the current screen; `volatility_20` failed on two held-out series. These remain conditional research results because regime, sector and multiple-testing evidence is incomplete. Relative momentum, preferred spreads and breadth require separate aligned panels before universal status can be claimed.

## Portfolio and metric methodology

The local file has explicit `real` or `demo` mode. Real mode requires all privacy-sensitive position fields; absent/incomplete local data cannot silently become a real portfolio. The checked run used demo mode and therefore is not a user result.

Native production-candidate risk methodology uses daily total returns on common dates, sample variance (`ddof=1`), 252-session annualization and zero risk-free rate unless configured. okama uses monthly aggregation and its nonlinear monthly annualization. On the public equal-weight demo panel, isolated okama reproduced CAGR 4.7108%, annualized monthly return 5.7304%, annualized risk 15.0614%, and terminal wealth 1.079740. Native daily results were CAGR 4.9521%, arithmetic return 6.5365%, volatility 18.4805%, with identical terminal wealth. Frequency and annualization—not convenient result selection—explain the differences. Daily total-return methodology remains primary; monthly okama is a reconciliation view.

## External methods

- PyPortfolioOpt commit `a6638d2e06dae6f444fd022cfd4b3c528902a85b`, MIT: 279 passed, 33 skipped, 5 failed. All failures were HRP calls to removed SciPy 1.18 private API `_LINKAGE_METHODS`. No runtime dependency added. Covariance shrinkage is clean-room implemented; convex mean-return/Black–Litterman remain experimental.
- vectorbt commit `34b6d5935e3ea3eccd549e2592bc0f455b8045f5`, Apache 2.0 with Commons Clause: installation failed on a Windows long-path JupyterLab asset, leaving pytest unavailable. It is not claimed runnable and is rejected as a required dependency.
- backtrader commit `b853d7c90b6721476eb5a5ea3135224e33db1f14`, GPL-3.0+: editable install/import succeeded as 1.9.78.123. GPL code was not copied. A clean-room native next-session fill simulator covers commission, slippage and unavailable final session; no broker connection exists.

## Issuer intelligence and dividends

Nine issuer-specific adapter classes map 81 metrics to official issuer/disclosure sources. Each contract requires publication date, `available_from`, reporting standard and validation. IFRS and RAS are never silently mixed. This stage is source discovery/schema mapping, not a claim that all documents are parsed.

Dividend outlook separates historical confirmed rows from conservative/base/optimistic scenarios. Scenario DPS is explicitly `estimated_not_announced`; yield on cost is computed only when a local average price exists.

## Scenario Engine 2.0

The implemented first validated proxy is IMOEX sensitivity using common-date total returns, a 20-session block bootstrap, 90% range and half-sample structural-break diagnostic. Rate, ZCYC, FX, oil and sector scenarios remain unavailable until their PIT proxy series pass source validation; fixed invented coefficients are not substituted.

## Limitations

The current portfolio is demo. No alpha candidate is production-ready. Regime discovery is a two-state empirical slice, not a validated HMM. PyPortfolioOpt HRP currently breaks with latest SciPy. vectorbt could not be installed on this host. Fundamental documents are mapped but not yet parsed. Scenario v2 currently validates only market beta.
