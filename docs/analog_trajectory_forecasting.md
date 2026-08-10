# Analog Trajectory Forecasting

Stage 45 stores only observed post-match price paths from canonical daily prices. Every path is normalized to `T0 = 100`; missing future sessions are left unavailable and are never interpolated. Terminal summaries cover 1, 5, 20, 60, 120 and 250 sessions and include central tendency, 10/25/50/75/90 percentiles, directional fractions, adverse/favorable excursion and dispersion.

The terminal-price translation is a historical-equivalent reference (`current price × historical median return`), never a target price. Directional fractions are descriptive analog consensus and are not published probabilities.

Strict-OOS replay uses expanding history. For each simulated cutoff, state matching, episode selection and baseline estimation use only earlier observations; the actual future is opened only after the forecast record is formed. Results remain research-only. Production models and the probability gate are unchanged.

## Reproduced run

The full run at cutoff `2026-08-07` produced 737,750 observed trajectory points, 384 ready terminal distributions and 30,306 strict-OOS replay records. Eight portfolio instruments had sufficient canonical history; X5 remained unavailable under the frozen minimum-history policy. A second run reproduced the same deterministic run id (`c9e5a0ac29df96de1758`) and exact row counts. Duplicate trajectory/replay keys and replay records whose training boundary reached their cutoff were both zero.

These replay results are evidence inputs for the broader validation in Stage 49, not model-selection conclusions. Several instrument/horizon cells do not beat the unconditional baseline on absolute error, so Stage 45 does not claim predictive advantage or production eligibility.
