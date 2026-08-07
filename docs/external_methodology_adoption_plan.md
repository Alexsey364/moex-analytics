# External methodology adoption plan

Adopt now: normalized provider contracts, provenance/freshness states, wealth-index and standard risk definitions, and strict feed/strategy separation. These are independently implemented concepts, not copied code.

Research further: okama as an optional comparator; okama-macro release-date semantics; AlgoPack availability and terms; event-driven backtesting on identical PIT data.

Reject now: external forecast scores without purged temporal validation, equal samples, baseline and OOS; synthetic fallback; automatic production promotion; AGPL/no-license source copying.

Next-stage gate: a method must reproduce on exported public price data, match metric definitions and dates, beat the local baseline out of sample, remain stable across folds, and pass legal review.
