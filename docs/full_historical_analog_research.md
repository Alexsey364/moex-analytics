# Full Historical Analog Research Run

`RUN_HISTORICAL_ANALOG_RESEARCH.bat` invokes the real `run-historical-analog-research` CLI command. The resumable sequence is data readiness, regimes, analog states, observed analog paths, PIT-safe event conditioning, frozen-policy fusion, frozen-holdout validation and atomic evidence-report generation.

Each checkpoint is persisted in DuckDB with status, result and runtime. Re-running a completed deterministic run returns the stored result; a failed step can resume without relabelling partial evidence as completed. The generated report is local at `reports/historical_analog_research_evidence.md` and remains Git-ignored.

The runner never changes production models, the Decision Engine or probability policy. Invalid temporal-leakage runs remain audit records and are excluded from report evidence except for the explicitly labelled leakage comparison.
