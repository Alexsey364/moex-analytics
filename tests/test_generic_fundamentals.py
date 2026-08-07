from datetime import date, datetime

import duckdb

from moex_analytics.fundamentals.generic import ensure_generic_schema, update_coverage


def _value(con, status="validated"):
    con.execute(
        """INSERT INTO issuer_fundamental_values
        (secid,metric,reporting_standard,period_start,period_end,publication_date,
         available_from,source,document,page_table,raw_value,normalized_value,unit,
         validation_status,revision,issuer)
        VALUES ('TEST','net_profit','IFRS',DATE '2024-01-01',DATE '2024-12-31',
        DATE '2025-03-01',?,'official','official','HTML:Net profit',10,10,'RUB',?,'original','TEST')""",
        [datetime(2025, 3, 2), status],
    )


def test_generic_schema_and_point_in_time_coverage():
    con = duckdb.connect(":memory:")
    con.execute(
        """CREATE TABLE issuer_fundamental_values(
        secid VARCHAR,metric VARCHAR,reporting_standard VARCHAR,period_start DATE,
        period_end DATE,publication_date DATE,available_from TIMESTAMP,source VARCHAR,
        document VARCHAR,page_table VARCHAR,raw_value DOUBLE,normalized_value DOUBLE,
        unit VARCHAR,validation_status VARCHAR,revision VARCHAR,
        PRIMARY KEY(secid,metric,period_end,reporting_standard,revision))"""
    )
    ensure_generic_schema(con)
    _value(con)
    result = update_coverage(con, "TEST")
    assert result["validated"] == 0  # no orphan value is counted without a source document
    assert (
        date(2024, 12, 31)
        not in con.execute(
            "SELECT latest_period FROM issuer_fundamental_coverage WHERE issuer='TEST'"
        ).fetchone()
    )
