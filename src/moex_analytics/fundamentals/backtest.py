"""Expanding-window historical valuation on a common sample."""

from __future__ import annotations

import statistics

import duckdb

VERSION = "sber-fundamental-backtest-v1"


def run(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(
        "DELETE FROM fundamental_backtest_results WHERE secid='SBER' AND calculation_version=?", [VERSION]
    )
    con.execute(
        "DELETE FROM fundamental_backtest_errors WHERE secid='SBER' AND calculation_version=?", [VERSION]
    )
    rows = con.execute(
        """SELECT trade_date,report_period_end,publication_date,
        max(value) FILTER(metric_id='eps') eps,max(value) FILTER(metric_id='bvps') bvps,
        max(value) FILTER(metric_id='pe') pe,max(value) FILTER(metric_id='pb') pb
        FROM fundamental_features WHERE secid='SBER'
        GROUP BY trade_date,report_period_end,publication_date
        HAVING eps IS NOT NULL AND bvps IS NOT NULL ORDER BY trade_date"""
    ).fetchall()
    prior_pe, prior_pb, written = [], [], 0
    for trade, period, _published, earnings, bvps, observed_pe, observed_pb in rows:
        price = con.execute(
            "SELECT close FROM canonical_daily_prices WHERE canonical_secid='SBER' AND trade_date=?", [trade]
        ).fetchone()[0]
        prices = con.execute(
            """SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='SBER'
            AND trade_date>? ORDER BY trade_date LIMIT 250""",
            [trade],
        ).fetchall()
        if prior_pe and prior_pb:
            estimates = {
                "naive": price,
                "pe": earnings * statistics.median(prior_pe),
                "pb_roe": bvps * statistics.median(prior_pb),
            }
            estimates["fundamental_ensemble"] = statistics.median([estimates["pe"], estimates["pb_roe"]])
            low, high = min(estimates["pe"], estimates["pb_roe"]), max(estimates["pe"], estimates["pb_roe"])
            for horizon in (20, 60, 120, 250):
                if len(prices) < horizon:
                    continue
                future = prices[horizon - 1][1]
                for method, estimate in estimates.items():
                    error = estimate - future
                    con.execute(
                        """INSERT INTO fundamental_backtest_results VALUES
                        (?,'SBER',?,?,?,?,?,?,?,?,?,?,'RAS annual',?,current_timestamp)""",
                        [
                            trade,
                            method,
                            horizon,
                            price,
                            estimate,
                            low,
                            high,
                            future,
                            future / price - 1,
                            None,
                            str(period),
                            VERSION,
                        ],
                    )
                    con.execute(
                        """INSERT INTO fundamental_backtest_errors VALUES
                        (?,'SBER',?,?,?,?,?,?,?,?,?,current_timestamp)""",
                        [
                            trade,
                            method,
                            horizon,
                            abs(error),
                            abs(error) / future,
                            (estimate / price - 1) - (future / price - 1),
                            (estimate >= price) == (future >= price),
                            low <= future <= high,
                            None,
                            VERSION,
                        ],
                    )
                    written += 1
        if observed_pe > 0:
            prior_pe.append(observed_pe)
        if observed_pb > 0:
            prior_pb.append(observed_pb)
    con.execute("DELETE FROM fundamental_model_comparison WHERE calculation_version=?", [VERSION])
    groups = con.execute(
        """SELECT CASE WHEN valuation_date<'2014-01-01' THEN 'pre-2014'
        WHEN valuation_date<'2022-01-01' THEN '2014-2021'
        WHEN valuation_date<'2024-01-01' THEN '2022-2023' ELSE 'holdout-2024+' END period,
        method,horizon,count(*),avg(absolute_error),avg(percentage_error),
        avg(CAST(direction_correct AS INTEGER)),avg(CAST(interval_hit AS INTEGER))
        FROM fundamental_backtest_errors WHERE calculation_version=? GROUP BY period,method,horizon""",
        [VERSION],
    ).fetchall()
    for period, model, horizon, sample, mae, mape, sign, coverage in groups:
        con.execute(
            "INSERT INTO fundamental_model_comparison VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",
            [period, model, horizon, sample, mae, mape, sign, coverage, None, VERSION],
        )
    return {"valuations": len(rows), "rows": written, "comparisons": len(groups)}
