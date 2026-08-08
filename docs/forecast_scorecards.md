# Forecast Scorecards and Learning Journal

Forecasts are captured only from the latest real immutable daily intelligence report. The system does not
backfill opinions that were not recorded at the time. A deterministic ID makes repeated capture at the same
logical cutoff a `no_change` operation.

Maturity uses observed MOEX trading sessions after the cutoff, never calendar-day offsets. Pending outcomes
are rechecked but a matured outcome is written once and retained. Neutral forecasts are evaluated against a
separate neutral band and are not counted as ordinary directional hits.

Live validation policy:

- fewer than 20 matured forecasts: `insufficient_live_sample`;
- 20–49: `accumulating_live_evidence`;
- at least 50 with directional hit rate at or above 55%: `promising_live_candidate`;
- at least 50 with hit rate below 45%: `degraded`;
- otherwise: `accumulating_live_evidence`.

`confirmed_live_candidate` is intentionally unavailable at this stage: confirmation requires a later,
explicitly approved governance policy with larger samples, calibration and regime stability. Probability
metrics remain null when probability disclosure is not allowed.

Decision and portfolio evaluations are labelled `hypothetical`; no broker orders or reconstructed trades are
created. Journal error categories are deterministic associations from stored evidence and outcomes. They do
not claim that an evidence block caused an error.
