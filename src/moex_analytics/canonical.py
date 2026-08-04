"""Build a traceable one-row-per-date canonical price series."""

from __future__ import annotations

import duckdb


def build_canonical(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Rebuild canonical prices; return (rows, overlapping-date conflicts)."""
    con.execute("DELETE FROM data_quality_issues WHERE issue_type='canonical_board_conflict'")
    conflicts = con.execute(
        """SELECT count(*) FROM (
             SELECT p.trade_date,s.canonical_secid
             FROM daily_prices p JOIN instrument_history_segments s
               ON p.secid=s.source_secid AND p.board=s.board
              AND p.trade_date BETWEEN s.date_from AND s.date_to
             GROUP BY p.trade_date,s.canonical_secid HAVING count(*) > 1
           )"""
    ).fetchone()[0]
    con.execute("DELETE FROM canonical_daily_prices")
    con.execute(
        """INSERT INTO canonical_daily_prices
         SELECT trade_date,canonical_secid,secid AS source_secid,board,open,high,low,close,
                  weighted_average_price,volume,value,number_of_trades,priority,loaded_at
           FROM (
             SELECT p.*,s.canonical_secid,s.priority,
                    row_number() OVER (
                      PARTITION BY p.trade_date,s.canonical_secid
                      ORDER BY s.priority DESC,s.board
                    ) AS choice
             FROM daily_prices p JOIN instrument_history_segments s
               ON p.secid=s.source_secid AND p.board=s.board
              AND p.trade_date BETWEEN s.date_from AND s.date_to
           ) ranked WHERE choice=1"""
    )
    rows = con.execute("SELECT count(*) FROM canonical_daily_prices").fetchone()[0]
    if conflicts:
        con.execute(
            """INSERT INTO data_quality_issues
               (secid,trade_date,issue_type,description,detected_at)
               SELECT canonical_secid,trade_date,'canonical_board_conflict',
                      'Multiple eligible boards; highest explicit priority selected',
                      current_timestamp
               FROM (
                 SELECT p.trade_date,s.canonical_secid
                 FROM daily_prices p JOIN instrument_history_segments s
                   ON p.secid=s.source_secid AND p.board=s.board
                  AND p.trade_date BETWEEN s.date_from AND s.date_to
                 GROUP BY p.trade_date,s.canonical_secid HAVING count(*) > 1
               )"""
        )
    return int(rows), int(conflicts)
