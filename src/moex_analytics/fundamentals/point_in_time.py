"""Daily snapshots without release or revision leakage."""

import duckdb

from . import CALCULATION_VERSION


def build_snapshots(con: duckdb.DuckDBPyConnection, version: str = CALCULATION_VERSION) -> int:
    con.execute("DELETE FROM fundamental_snapshots WHERE secid='SBER' AND calculation_version=?", [version])
    con.execute(
        """INSERT INTO fundamental_snapshots
    SELECT p.trade_date,'SBER',o.metric_id,o.value,o.period_end,o.publication_date,
    date_diff('day',o.period_end,p.trade_date),o.source,?
    FROM (SELECT DISTINCT trade_date FROM canonical_daily_prices WHERE canonical_secid='SBER') p
    JOIN LATERAL (SELECT * FROM fundamental_observations o WHERE o.secid='SBER'
      AND CAST(o.available_from AS DATE)<=p.trade_date
      QUALIFY row_number() OVER(PARTITION BY metric_id,accounting_standard
      ORDER BY available_from DESC,period_end DESC,revision_id DESC)=1) o ON true""",
        [version],
    )
    return con.execute(
        "SELECT count(*) FROM fundamental_snapshots WHERE calculation_version=?", [version]
    ).fetchone()[0]
