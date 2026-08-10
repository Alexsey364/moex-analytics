# Historical Analog Search Engine 3.0

Stage 44 separates market, issuer and portfolio analog contexts. Robust scaling, covariance and PCA are fitted only on historical rows before the current cutoff. State methods and pre-cutoff path methods are stored separately. DTW is a research pilot, never a synthetic trajectory.

Selected analog dates are separated by at least 20 sessions (or the path window) so adjacent crisis days do not masquerade as independent evidence. Each match stores coverage, regime/event agreement, distance percentile and ranked similarity/difference decomposition. Learned similarity is explicitly deferred until strict temporal validation.

## Frozen sparse-data and numerical policy

- A context needs at least 500 historical observations and 60% coverage of its required features.
- Requested nearest-neighbour depth is 50. Selected dates must be separated by at least 20 sessions; a method is marked `insufficient_independent_episodes` when that depth cannot be supported.
- Robust scaling, Ledoit-Wolf covariance and PCA are fitted exclusively on rows before the cutoff. Mahalanobis distance requires at least 50 rows and five rows per feature. Singular or ill-conditioned covariance is reported as `numerical_failure`, not silently replaced by another metric.
- PCA needs at least 20 training rows. Cosine distance with a zero-norm vector and short path histories are recorded as `method_unavailable`.
- Empty regime or event filters produce an empty eligible sample. They never fall back to an unfiltered universe.
- An unavailable optional method does not invalidate otherwise usable contexts. Every method retains its own status and reason.

## Reproduced full run

The Stage 44 full run used cutoff `2026-08-07` and deterministic run id `157585b2d4ef1660604a`. It created 11 contexts: 10 `ready` and one `insufficient_history`. The engine stored 3,563 independent analog observations: 392 market, 2,971 issuer and 200 portfolio observations.

All four state/path families were exercised: robust Euclidean, regularized Mahalanobis, cosine, train-only PCA, path cosine (20/60/120 sessions) and DTW (20 sessions). Method-level sparse-history cases remained explicitly unavailable. No selected analog date was on or after its cutoff, and a second identical run reproduced the same run id and row counts without duplicates.
