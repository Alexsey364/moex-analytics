# Actual Historical Data Backfill

Stage 20.5 downloads bytes from official sources, hashes them, records point-in-time availability and stores only observations supported by an explicit parser contract. Raw bytes and DuckDB remain outside Git.

## Fundamental checkpoint

The first implemented multi-period source is the official Moscow Exchange annual-results archive. Releases for FY2016–FY2019 are downloaded from `moex.com`, stored under `data/raw/fundamentals_actual`, hashed with SHA-256, and parsed only when both the expected label and published numeric token occur in the downloaded bytes.

Values that fail that contract are written to `actual_manual_review_candidates` with issuer, metric, period, publication date, URL, hash, table/label, candidate value, unit and reason. They are never treated as validated automatically.

The generic SBER history is reused without weakening its existing validation. FIVE and X5 remain separate; no legal or price continuity is assumed.

## Universe policy

`tradable_on_date_universe` means only that a security has an official MOEX trade observation on that date. It is not index membership or sector membership. The pilot is restricted to Russian inactive common/preferred shares and records requests, rows, errors, elapsed time and disk size.

## External factors and contracts

USD/RUB, EUR/RUB and CNY/RUB use the official Bank of Russia XML endpoint with effective-date availability. Dividend history uses MOEX ISS. Futures specification rows retain the official payload hash. Basis remains disabled until spot/futures scale and underlying-unit equivalence are proven.
