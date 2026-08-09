"""Official sector, rates, FX and bounded derivatives context for Stage 30."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .schema import DDL

SECTOR_SERIES = {
    "moex_oil_gas", "moex_finance", "moex_consumer", "moex_metals",
    "moex_power", "moex_transport",
}
RATE_SERIES = {
    "cbr_key_rate", "cbr_ruonia", "moex_rusfar", "moex_ofz_3y",
    "moex_ofz_5y", "moex_ofz_10y",
}
FX_SERIES = {"cbr_usd_rub", "cbr_eur_rub", "cbr_cny_rub", "moex_cny_rub"}


def ensure_schema(con) -> None:
    con.execute(DDL)


def _build_context(con) -> int:
    con.execute("DELETE FROM stage30_context_features")
    allowed = sorted(SECTOR_SERIES | RATE_SERIES | FX_SERIES)
    marks = ",".join("?" for _ in allowed)
    con.execute(
        f"""INSERT INTO stage30_context_features
        WITH base AS (
          SELECT observation_date,series_id,value,available_from,source,
          lag(value,1) OVER(PARTITION BY series_id ORDER BY observation_date) p1,
          lag(value,5) OVER(PARTITION BY series_id ORDER BY observation_date) p5,
          lag(value,20) OVER(PARTITION BY series_id ORDER BY observation_date) p20,
          lag(value,60) OVER(PARTITION BY series_id ORDER BY observation_date) p60,
          lag(value,120) OVER(PARTITION BY series_id ORDER BY observation_date) p120
          FROM macro_observations WHERE series_id IN ({marks})
          QUALIFY row_number() OVER(PARTITION BY series_id,observation_date
                                    ORDER BY loaded_at DESC,vintage DESC)=1
        ), long AS (
          SELECT observation_date,series_id,available_from,source,feature_name,feature_value
          FROM base UNPIVOT(feature_value FOR feature_name IN
            (value AS level,p1 AS previous_1,p5 AS previous_5,p20 AS previous_20,
             p60 AS previous_60,p120 AS previous_120))
        )
        SELECT observation_date,series_id,feature_name,
        CASE WHEN feature_name='level' THEN feature_value
             ELSE (SELECT b.value FROM base b WHERE b.observation_date=long.observation_date
                   AND b.series_id=long.series_id)/nullif(feature_value,0)-1 END,
        available_from,source,
        CASE WHEN available_from IS NOT NULL AND CAST(available_from AS DATE)>=observation_date
             THEN 'pit_valid' ELSE 'timestamp_review' END,current_timestamp FROM long""",
        allowed,
    )
    # Spreads/slopes use only observations available on the date.
    con.execute(
        """INSERT OR REPLACE INTO stage30_context_features
        SELECT k.observation_date,'derived_rates',feature_name,feature_value,
        greatest(k.available_from,o.available_from),'CBR/MOEX official','pit_valid',current_timestamp
        FROM macro_observations k JOIN macro_observations o USING(observation_date)
        CROSS JOIN LATERAL (VALUES
          ('ruonia_key_spread',o.value-k.value)) v(feature_name,feature_value)
        WHERE k.series_id='cbr_key_rate' AND o.series_id='cbr_ruonia'
        QUALIFY row_number() OVER(PARTITION BY k.observation_date
                                  ORDER BY k.loaded_at DESC,o.loaded_at DESC)=1"""
    )
    con.execute(
        """INSERT OR REPLACE INTO stage30_context_features
        SELECT y3.observation_date,'derived_curve',feature_name,feature_value,
        greatest(y3.available_from,y5.available_from,y10.available_from),
        'MOEX official','pit_valid',current_timestamp
        FROM macro_observations y3 JOIN macro_observations y5 USING(observation_date)
        JOIN macro_observations y10 USING(observation_date)
        CROSS JOIN LATERAL (VALUES ('3s5s',y5.value-y3.value),
          ('3s10s',y10.value-y3.value),('5s10s',y10.value-y5.value),
          ('curvature',2*y5.value-y3.value-y10.value)) v(feature_name,feature_value)
        WHERE y3.series_id='moex_ofz_3y' AND y5.series_id='moex_ofz_5y'
        AND y10.series_id='moex_ofz_10y'
        QUALIFY row_number() OVER(PARTITION BY y3.observation_date
          ORDER BY y3.loaded_at DESC,y5.loaded_at DESC,y10.loaded_at DESC)=1"""
    )
    return con.execute("SELECT count(*) FROM stage30_context_features").fetchone()[0]


def _build_coverage(con) -> int:
    con.execute("DELETE FROM stage30_context_coverage")
    for family, series in (("sector", SECTOR_SERIES), ("rates", RATE_SERIES), ("fx", FX_SERIES)):
        for series_id in sorted(series):
            row = con.execute(
                """SELECT count(*),min(observation_date),max(observation_date),any_value(source),
                count(*) FILTER(available_from IS NULL) FROM macro_observations
                WHERE series_id=?""", [series_id]
            ).fetchone()
            count, earliest, latest, source, missing_available = row
            con.execute(
                "INSERT INTO stage30_context_coverage VALUES (?,?,?,?,?,?,'official/free',?,?,?,current_timestamp)",
                [family, series_id, count, earliest, latest, source,
                 "validated" if count and not missing_available else "partial",
                 "usable" if count else "missing",
                 None if count else "official series not loaded"],
            )
    for series_id, family, limitation in (
        ("brent", "commodities", "no validated free PIT series loaded"),
        ("urals", "commodities", "requires_paid_data"),
        ("fertilizer_proxy", "commodities", "requires_paid_data"),
    ):
        con.execute(
            """INSERT INTO stage30_context_coverage VALUES
            (?,?,0,NULL,NULL,NULL,?, 'missing','requires_paid_data',?,current_timestamp)""",
            [family, series_id, "paid/restricted" if "paid" in limitation else "unproven", limitation],
        )
    return con.execute("SELECT count(*) FROM stage30_context_coverage").fetchone()[0]


def _build_futures(con) -> int:
    con.execute("DELETE FROM stage30_futures_features")
    con.execute(
        """INSERT INTO stage30_futures_features
        SELECT d.trade_date,d.secid,d.open_interest,
        d.open_interest-lag(d.open_interest) OVER(PARTITION BY d.secid ORDER BY d.trade_date),
        d.volume,d.volume/nullif(d.open_interest,0),date_diff('day',d.trade_date,c.expiration),
        FALSE,NULL,'units_unvalidated_basis_disabled',current_timestamp
        FROM sber_futures_daily d LEFT JOIN sber_futures_contracts c USING(secid)"""
    )
    return con.execute("SELECT count(*) FROM stage30_futures_features").fetchone()[0]


def _evaluate_pilots(con) -> tuple[int, int]:
    con.execute("DELETE FROM stage30_pilot_evaluations")
    options = con.execute(
        """SELECT count(*),min(first_trade),max(last_trade),
        count(*) FILTER(history_accessible AND implied_volatility IS NOT NULL)
        FROM moex_options_audit WHERE underlying IN ('SBER','SBERP','IMOEX')"""
    ).fetchone()
    intraday = con.execute(
        "SELECT count(*),min(trade_date),max(trade_date),count(*) FROM intraday_features WHERE secid='SBER'"
    ).fetchone()
    for name, row, limitation in (
        ("options", options, "historical option chain unavailable; no IV ablation possible"),
        ("intraday", intraday, "same-sample walk-forward ablation required before expansion"),
    ):
        count, earliest, latest, usable = row
        status = "pending_ablation" if usable else "insufficient_sample"
        decision = "pilot_only" if usable else "do_not_expand"
        con.execute(
            "INSERT INTO stage30_pilot_evaluations VALUES (?,?,?,?,?,?,?,?,current_timestamp)",
            [name, count, earliest, latest, usable, status, decision, limitation],
        )
    return int(options[0]), int(intraday[0])


def build_validated_market_context(con) -> dict:
    ensure_schema(con)
    run_id = hashlib.sha256(f"context:{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:20]
    context_rows = _build_context(con)
    coverage = _build_coverage(con)
    futures = _build_futures(con)
    options, intraday = _evaluate_pilots(con)
    unresolved = con.execute(
        "SELECT count(*) FROM market_history_quality_issues WHERE issue_type="
        "'large_return_corporate_action_review' AND status!='resolved'"
    ).fetchone()[0]
    details = {"historical_sector_membership_assumed": False,
               "futures_basis_enabled": False, "options_mass_backfill": False,
               "intraday_mass_backfill": False, "commodity_sources": "gated"}
    con.execute(
        "INSERT INTO stage30_context_runs VALUES (?,current_timestamp,'completed',?,?,?,?,?,?,?,0)",
        [run_id, context_rows, coverage, futures, options, intraday, unresolved,
         json.dumps(details)],
    )
    return {"run_id": run_id, "context_rows": context_rows, "coverage_series": coverage,
            "futures_rows": futures, "options_catalog_rows": options,
            "intraday_feature_rows": intraday, "unresolved_corporate_actions": unresolved,
            **details, "production_changes": 0}


def market_context_status(con) -> dict:
    ensure_schema(con)
    latest = con.execute(
        "SELECT * FROM stage30_context_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"latest": latest, "coverage": con.execute(
        "SELECT dataset_family,quality_status,count(*) FROM stage30_context_coverage GROUP BY 1,2"
    ).fetchall(), "pilots": con.execute("SELECT * FROM stage30_pilot_evaluations").fetchall()}
