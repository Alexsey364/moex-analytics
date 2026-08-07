# External projects reproduction

All commands ran under `C:\Users\User\AppData\Local\Temp\moex_external_audit_13`; no project database or private portfolio was exposed. Host Python for okama was 3.12; okama-macro venv resolved to the installed Python 3.13 launcher. Dependencies were resolved only inside `.audit-venv`.

| Project | Commit | License | Command / result | Reproduced | Decision |
|---|---|---|---|---|---|
| okama | `af05da8e90dbbfe9a4092c3fe484ea309f64d0d9` | MIT | `python -m pytest -q`: first 109 passed, 3 skipped, 298 errors (`fixture 'mocker' not found`); after declared dev deps: 407 passed, 3 skipped | risk/frontier unit behavior | use_as_dependency |
| okama-macro | `c0e553cfefd7d3afb5513d311f99b06b1e8a8950` | MIT | isolated editable install; `python -m pytest -q`: 89 passed | adapter/parser tests | research_further |
| openbb-forecast | `115199995dbf8409da6cd19a9708f959339a7569` | AGPL-3.0 | not installed: only two test files, stale project, no equal-sample MOEX OOS demonstration | no | reject |
| russian-markets-lab | `7756fc7f268eb9746a2ad709d31db6dfe4894092` | none found | not executed: no legal reuse permission and application stack is not needed to verify formulas | no | research_further |
| backtrader_moexalgo | `e16ec9272f5b02b9f863dbeff0fe84a7b4a4eed3` | MIT | repository has no test suite; examples require external feed setup | no claim | research_further |

The initial Git inspection emitted Windows `dubious ownership`; inspection used per-command `git -c safe.directory=<exact temp clone>` without changing global Git configuration.

Projects not shown as reproduced were not claimed to work. No notebook/backtest forecast was accepted because none supplied the required identical sample, temporal validation, baseline, and leakage evidence.

| Проект | Метод | Удалось запустить | Воспроизведено | Лучше нашей системы | Решение | Лицензия |
|---|---|---:|---:|---:|---|---|
| okama | portfolio risk/frontier | yes | yes | equivalent definitions, not superior | use_as_dependency | MIT |
| okama-macro | macro adapters | yes | tests only | not established | research_further | MIT |
| OpenBB | provider architecture | not full stack | no | not comparable | reimplement | AGPL-3.0 |
| openbb-forecast | forecasting | no | no | no evidence | reject | AGPL-3.0 |
| russian-markets-lab | provenance/status UI | no | no | not comparable | research_further | none |
| Island Model | ensemble forecast | no | no | no accepted evidence | reject | none |
| backtrader_moexalgo | event backtest | no tested example | no | not established | research_further | MIT |
