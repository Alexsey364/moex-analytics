# Open-source project audit

Audit date: 2026-08-07. No source code was copied. Repository metadata and licenses were checked at the commits recorded in `external_projects_reproduction.md`.

| Project | Main value | PIT / leakage assessment | Decision |
|---|---|---|---|
| mbk-dev/okama | Wealth, drawdown, rolling risk, frontiers | Portfolio statistics are reproducible; forecasting is not its claim | use_as_dependency |
| mbk-dev/okama-macro | Macro adapters | Source dates still need local PIT validation | research_further |
| OpenBB-finance/OpenBB | Provider contracts and normalized schemas | Architecture useful; provider data semantics vary | reimplement |
| openbb-forecast | Forecast extension examples | No accepted equal-sample MOEX pseudo-OOS evidence | reject for forecasting |
| russian-markets-lab | Provenance and data-quality visibility | No license; results not independently established | research_further / reference only |
| artemleonich/Moex | Walk-forward and ensemble ideas | Synthetic fallback and published scores are not evidence | reject results; reference only |
| backtrader_moexalgo | Feed/strategy separation | Event timing needs independent audit | reimplement architecture |
| fertkir/portfolio-allocation | Allocation examples | Archived GPL project | reference only |
| moexalgo/moexalgo | AlgoPack access | Useful only where data terms and availability fit | use_as_dependency after legal/data review |

Security rule: external programs received no secrets, broker keys, private positions, user portfolio, or project DuckDB. Heavy/no-license projects were inspected but not executed against user data.
