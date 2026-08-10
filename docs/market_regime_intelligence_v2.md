# Market Regime Intelligence 2.0

Stage 43 is research-only. It creates multi-scale market and issuer state vectors from persisted PIT data, then compares KMeans, diagonal Gaussian Mixture, and a Gaussian-emission Markov decoder for 2–6 states.

All scaling and model fitting use the chronological training segment only. The latest 20% is an OOS reproducibility segment. Selection uses silhouette stability, persistence, minimum cluster size and train/test reproducibility; future returns are never used to label or select regimes.

Regime transitions are historical frequencies, not stock forecast probabilities. Conditional returns are explicitly stored as associations, not causes. Novelty is a robust distance percentile from the training distribution.

## Persistence contract

`regime_model_candidates` has 13 named fields: run/model identity (3), chronological sample sizes (2), six diagnostics, `selected`, and `status`. The initial implementation accidentally used 12 placeholders for all 13 values. Persistence now uses an explicit named-column mapping from `RegimeCandidateRecord`; `ensure_schema` verifies the complete contract and safely adds `status` only for an older table missing that audit field. Every Stage 43 insert names its destination columns and is idempotent.
