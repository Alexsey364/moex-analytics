"""Resumable point-in-time MOEX trading-history research layer (Stage 21)."""
# ruff: noqa: E501 -- long SQL expressions are kept readable and auditable.

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import numpy as np

from .actual_backfill.schema import DDL
from .config import PROJECT_ROOT
from .database import database_path
from .moex_client import MoexClient

RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "market_history"
FIELDS = (
    "TRADEDATE",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "LEGALCLOSEPRICE",
    "WAPRICE",
    "MARKETPRICE",
    "MARKETPRICE2",
    "ADMITTEDQUOTE",
    "VALUE",
    "VOLUME",
    "NUMTRADES",
    "TRADINGSESSION",
    "BOARDID",
    "CURRENCYID",
)


def ensure_schema(con) -> None:
    con.execute(DDL)


def _rows(block):
    return [dict(zip(block.get("columns", []), row, strict=True)) for row in block.get("data", [])]


def eligible_universe(con) -> list[str]:
    """Actual shares only; fund/bond/technical groups never enter implicitly."""
    return [
        r[0]
        for r in con.execute(
            "SELECT secid FROM historical_equity_universe WHERE instrument_type IN "
            "('common_share','preferred_share') AND (regnumber IS NOT NULL OR isin LIKE 'RU%') "
            "ORDER BY secid"
        ).fetchall()
    ]


def seed_jobs(con, client: MoexClient | None = None, limit: int | None = None) -> dict:
    ensure_schema(con)
    client = client or MoexClient()
    secids = eligible_universe(con)[:limit] if limit else eligible_universe(con)
    added = errors = 0
    for secid in secids:
        try:
            for board in client.discover_history(secid):
                if board.get("market") != "shares":
                    continue
                before = con.execute(
                    "SELECT count(*) FROM market_history_jobs WHERE secid=? AND boardid=?",
                    [secid, board["boardid"]],
                ).fetchone()[0]
                con.execute(
                    """INSERT OR IGNORE INTO market_history_jobs VALUES
                    (?,?,?,?,?,?,0,0,'pending',0,NULL,current_timestamp)""",
                    [
                        secid,
                        board["boardid"],
                        board["engine"],
                        board["market"],
                        board["history_from"],
                        board.get("history_till"),
                    ],
                )
                added += int(not before)
        except Exception:
            errors += 1
    return {"eligible": len(secids), "jobs_added": added, "discovery_errors": errors}


def _store_raw(payload: dict, secid: str, boardid: str, start: int) -> tuple[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    folder = RAW_ROOT / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.json"
    if not path.exists():
        path.write_bytes(raw)
    return digest, str(path.relative_to(PROJECT_ROOT))


def run_batch(
    con,
    *,
    client: MoexClient | None = None,
    jobs: int = 25,
    pages_per_job: int | None = None,
    pause: float = 0.05,
) -> dict:
    """Continue pending/failed jobs. Commit each page so interruption loses no completed work."""
    ensure_schema(con)
    client = client or MoexClient()
    run_id = uuid.uuid4().hex[:20]
    selected = con.execute(
        """SELECT secid,boardid,engine,market,history_from,
        coalesce(history_till,current_date),next_start FROM market_history_jobs
        WHERE status IN ('pending','running','failed') ORDER BY status,updated_at NULLS FIRST,secid
        LIMIT ?""",
        [jobs],
    ).fetchall()
    requests = received = inserted = failures = completed = 0
    for secid, board, engine, market, first, last, start in selected:
        con.execute(
            "UPDATE market_history_jobs SET status='running',attempts=attempts+1,"
            "updated_at=current_timestamp WHERE secid=? AND boardid=?",
            [secid, board],
        )
        page_no = 0
        while True:
            began = time.perf_counter()
            source = (
                f"{client.base_url}/history/engines/{engine}/"
                f"markets/{market}/boards/{board}/securities/{secid}.json"
            )
            try:
                payload = client.get_json(
                    source.removeprefix(client.base_url + "/"),
                    {
                        "from": str(first),
                        "till": str(last),
                        "start": start,
                        "iss.meta": "off",
                        "iss.only": "history,history.cursor",
                    },
                )
                digest, raw_path = _store_raw(payload, secid, board, start)
                history = _rows(payload.get("history", {}))
                requests += 1
                received += len(history)
                before = con.execute("SELECT count(*) FROM moex_equity_eod").fetchone()[0]
                values = []
                for row in history:
                    if not row.get("TRADEDATE"):
                        continue
                    values.append(
                        [row.get(k) for k in FIELDS[:14]]
                        + [
                            row.get("BOARDID") or board,
                            row.get("CURRENCYID"),
                            source,
                            digest,
                            datetime.now(UTC),
                        ]
                    )
                if values:
                    con.executemany(
                        """INSERT OR REPLACE INTO moex_equity_eod VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [[v[0], secid, v[14], v[13] or 0, *v[1:13], v[15], *v[16:]] for v in values],
                    )
                delta = con.execute("SELECT count(*) FROM moex_equity_eod").fetchone()[0] - before
                inserted += delta
                cursor = _rows(payload.get("history.cursor", {}))
                nxt = start + (cursor[0].get("PAGESIZE", len(history)) if cursor else len(history))
                total = cursor[0].get("TOTAL", len(history)) if cursor else len(history)
                done = not history or nxt >= total
                con.execute(
                    """UPDATE market_history_jobs SET next_start=?,rows_loaded=rows_loaded+?,
                    status=?,last_error=NULL,updated_at=current_timestamp WHERE secid=? AND boardid=?""",
                    [nxt, delta, "completed" if done else "pending", secid, board],
                )
                req_id = hashlib.sha256(f"{run_id}:{secid}:{board}:{start}".encode()).hexdigest()[:24]
                con.execute(
                    "INSERT OR REPLACE INTO market_history_requests VALUES "
                    "(?,?,?,?,?,current_timestamp,?,200,?,?,?,?, 'completed',NULL)",
                    [
                        req_id,
                        run_id,
                        secid,
                        board,
                        start,
                        time.perf_counter() - began,
                        len(history),
                        source,
                        digest,
                        raw_path,
                    ],
                )
                if done:
                    completed += 1
                    break
                start = nxt
                page_no += 1
                if pages_per_job is not None and page_no >= pages_per_job:
                    break
                if pause:
                    time.sleep(pause)
            except Exception as exc:
                failures += 1
                con.execute(
                    "UPDATE market_history_jobs SET status='failed',last_error=?,"
                    "updated_at=current_timestamp WHERE secid=? AND boardid=?",
                    [f"{type(exc).__name__}: {exc}", secid, board],
                )
                break
    return {
        "run_id": run_id,
        "jobs_selected": len(selected),
        "jobs_completed": completed,
        "requests": requests,
        "rows_received": received,
        "rows_inserted": inserted,
        "failures": failures,
    }


def build_trading_statistics(con) -> dict:
    ensure_schema(con)
    con.execute("DELETE FROM equity_board_history")
    con.execute("""INSERT INTO equity_board_history SELECT secid,boardid,min(trade_date),max(trade_date),
        count(*),sum(coalesce(value,0)),FALSE,NULL,current_timestamp FROM moex_equity_eod GROUP BY 1,2""")
    con.execute("""UPDATE equity_board_history b SET selected_for_chain=TRUE FROM
        (SELECT secid,boardid,row_number() over(PARTITION BY secid ORDER BY total_value DESC,
        observations DESC,boardid) n FROM equity_board_history) x
        WHERE b.secid=x.secid AND b.boardid=x.boardid AND x.n=1""")
    con.execute(
        "UPDATE equity_board_history SET exclusion_reason='lower_turnover_duplicate_board' "
        "WHERE NOT selected_for_chain"
    )
    con.execute("DELETE FROM equity_liquidity_daily")
    con.execute("""INSERT INTO equity_liquidity_daily
      WITH x AS (SELECT e.*, close/lag(close) over(PARTITION BY secid ORDER BY trade_date)-1 ret
       FROM moex_equity_eod e JOIN equity_board_history b USING(secid,boardid) WHERE b.selected_for_chain),
      r AS (SELECT *,avg(value) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) t5,
       avg(value) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) t20,
       avg(value) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) t60,
       avg(value) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) t120,
       avg(value) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) t250,
       avg(volume) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) v20,
       avg(num_trades) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) n20 FROM x)
      SELECT trade_date,secid,boardid,ret,value,volume,num_trades,value/nullif(num_trades,0),
       coalesce(volume,0)=0,abs(ret)/nullif(value,0),t5,t20,t60,t120,t250,v20,n20,
       percent_rank() over(PARTITION BY trade_date ORDER BY t20) FROM r""")
    con.execute("DELETE FROM market_breadth_daily")
    con.execute("""INSERT INTO market_breadth_daily
      WITH x AS (SELECT *,max(close) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) hi20,
       min(close) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) lo20,
       avg(close) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) sma20,
       avg(close) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) sma50,
       avg(close) over(PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) sma200,
       lag(close,20) over(PARTITION BY secid ORDER BY trade_date) p20,
       lag(close,60) over(PARTITION BY secid ORDER BY trade_date) p60
       FROM moex_equity_eod e JOIN equity_board_history b USING(secid,boardid) WHERE b.selected_for_chain)
      SELECT trade_date,count(*),count(*) filter(where close>lag_close),count(*) filter(where close<lag_close),
       count(*) filter(where close=lag_close),count(*) filter(where close>=hi20),count(*) filter(where close<=lo20),
       count(*) filter(where close>sma20),count(*) filter(where close>sma50),count(*) filter(where close>sma200),
       count(*) filter(where close>p20),count(*) filter(where close>p60),avg(close/lag_close-1),
       stddev_samp(close/lag_close-1),sum(value),sum(value) filter(where close>lag_close),
       sum(value) filter(where close<lag_close),current_timestamp FROM
       (SELECT x.*,lag(close) over(PARTITION BY secid ORDER BY trade_date) lag_close FROM x) q GROUP BY trade_date""")
    con.execute("DELETE FROM market_state_daily")
    con.execute("""INSERT INTO market_state_daily
      WITH raw AS (SELECT *,
       (advancing-declining)::DOUBLE/nullif(tradable_count,0) breadth,
       (advancing_turnover-declining_turnover)/nullif(total_turnover,0) risk_appetite,
       (above_sma50::DOUBLE/nullif(tradable_count,0)-.5)*2 trend,
       -return_dispersion dispersion,ln(nullif(total_turnover,0)) liquidity
       FROM market_breadth_daily),
      scores AS (SELECT *,
       (breadth-avg(breadth) over w)/nullif(stddev_samp(breadth) over w,0) b_score,
       (liquidity-avg(liquidity) over w)/nullif(stddev_samp(liquidity) over w,0) l_score,
       (dispersion-avg(dispersion) over w)/nullif(stddev_samp(dispersion) over w,0) v_score
       FROM raw WINDOW w AS (ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND 1 PRECEDING))
      SELECT trade_date,b_score,l_score,v_score,dispersion,trend,risk_appetite,
       CASE WHEN coalesce(b_score,0)>.5 AND trend>.2 THEN 'broad_risk_on'
            WHEN coalesce(b_score,0)<-.5 AND trend<-.2 THEN 'broad_risk_off'
            WHEN dispersion<-.03 THEN 'high_dispersion' ELSE 'mixed' END,
       json_object('breadth',breadth,'advancing',advancing,'declining',declining,
        'turnover',total_turnover,'trend',trend,'risk_appetite',risk_appetite),current_timestamp
      FROM scores""")
    return {
        "boards": con.execute("SELECT count(*) FROM equity_board_history").fetchone()[0],
        "liquidity_rows": con.execute("SELECT count(*) FROM equity_liquidity_daily").fetchone()[0],
        "breadth_days": con.execute("SELECT count(*) FROM market_breadth_daily").fetchone()[0],
        "market_state_days": con.execute("SELECT count(*) FROM market_state_daily").fetchone()[0],
    }


def coverage(con, *, save=False) -> dict:
    ensure_schema(con)
    row = con.execute(
        "SELECT count(distinct secid),count(distinct boardid),count(*),min(trade_date),max(trade_date) FROM moex_equity_eod"
    ).fetchone()
    states = dict(con.execute("SELECT status,count(*) FROM market_history_jobs GROUP BY status").fetchall())
    result = {
        "securities": row[0],
        "boards": row[1],
        "rows": row[2],
        "date_from": row[3],
        "date_to": row[4],
        "completed_jobs": states.get("completed", 0),
        "pending_jobs": states.get("pending", 0),
        "failed_jobs": states.get("failed", 0),
        "database_bytes": database_path().stat().st_size,
    }
    if save:
        con.execute(
            "INSERT INTO stage21_coverage_snapshots VALUES (?,current_timestamp,?,?,?,?,?,?,?,?,?,?)",
            [uuid.uuid4().hex[:20], *result.values(), json.dumps(result, default=str)],
        )
    return result


def backfill_official_market_series(
    con, date_from: date = date(1995, 1, 1), date_to: date | None = None
) -> dict:
    """Load official indices and traded FX without merging them with CBR fixing."""
    from .macro.repository import upsert_observations, upsert_series
    from .macro.sources import moex

    date_to = date_to or date.today()
    upsert_series(con, moex.definitions())
    result = {}
    for series_id in moex.INSTRUMENTS:
        latest = con.execute(
            "SELECT max(observation_date) FROM macro_observations WHERE series_id=?",
            [series_id],
        ).fetchone()[0]
        resume_from = max(date_from, latest - timedelta(days=7)) if latest else date_from
        rows = moex.download(series_id, str(resume_from), str(date_to))
        inserted = upsert_observations(con, rows)
        result[series_id] = {
            "received": len(rows),
            "inserted": inserted,
            "date_from": min((row.observation_date for row in rows), default=None),
            "date_to": max((row.observation_date for row in rows), default=None),
            "kind": "traded_exchange_series" if "rub" in series_id else "official_index",
            "resume_from": resume_from,
        }
    return result


def evaluate_market_factors(con, horizons=(1, 5, 20), folds=5) -> dict:
    """Same-sample expanding walk-forward screen; research only, never model promotion."""
    ensure_schema(con)
    prices = con.execute(
        "SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='SBER' ORDER BY trade_date"
    ).df()
    breadth = con.execute("""SELECT trade_date,
        (advancing-declining)::DOUBLE/nullif(tradable_count,0) breadth_balance,
        (advancing_turnover-declining_turnover)/nullif(total_turnover,0) turnover_balance,
        return_dispersion,total_turnover FROM market_breadth_daily ORDER BY trade_date""").df()
    macro = con.execute("""SELECT observation_date trade_date,series_id,value FROM macro_observations
        WHERE series_id IN ('moex_imoex','moex_rvi','moex_rusfar')
        QUALIFY row_number() over(PARTITION BY series_id,observation_date
        ORDER BY available_from DESC)=1""").df()
    if prices.empty or breadth.empty:
        return {"status": "insufficient_data", "evaluations": 0}
    macro = macro.pivot(index="trade_date", columns="series_id", values="value").reset_index()
    frame = prices.merge(breadth, on="trade_date", how="inner").merge(macro, on="trade_date", how="left")
    frame = frame.sort_values("trade_date")
    for column in ("moex_imoex", "moex_rvi", "moex_rusfar", "total_turnover"):
        if column in frame:
            frame[f"{column}_change"] = frame[column].pct_change(fill_method=None)
    features = [
        c
        for c in (
            "breadth_balance",
            "turnover_balance",
            "return_dispersion",
            "total_turnover_change",
            "moex_imoex_change",
            "moex_rvi_change",
            "moex_rusfar_change",
        )
        if c in frame
    ]
    con.execute("DELETE FROM stage21_factor_evaluation")
    rng = np.random.default_rng(21)
    written = useful = rejected = 0
    for horizon in horizons:
        work = frame.copy()
        work["target"] = work.close.shift(-horizon) / work.close - 1
        for feature in features:
            sample = work[["trade_date", feature, "target"]].replace([np.inf, -np.inf], np.nan).dropna()
            if len(sample) < max(60, folds * 12):
                continue
            boundaries = np.linspace(len(sample) // 3, len(sample), folds + 1, dtype=int)
            baseline_hits, model_hits, ics = [], [], []
            fold_wins = 0
            for idx in range(folds):
                train = sample.iloc[: boundaries[idx]]
                test = sample.iloc[boundaries[idx] : boundaries[idx + 1]]
                if test.empty or train[feature].std() == 0:
                    continue
                slope, intercept = np.polyfit(train[feature], train.target, 1)
                baseline = np.repeat(train.target.mean() >= 0, len(test))
                predicted = slope * test[feature].to_numpy() + intercept >= 0
                actual = test.target.to_numpy() >= 0
                bh, mh = (baseline == actual).astype(float), (predicted == actual).astype(float)
                baseline_hits.extend(bh)
                model_hits.extend(mh)
                fold_wins += int(mh.mean() > bh.mean())
                ics.append(float(test[[feature, "target"]].corr(method="spearman").iloc[0, 1]))
            if not model_hits:
                continue
            delta = np.asarray(model_hits) - np.asarray(baseline_hits)
            boot = [rng.choice(delta, len(delta), replace=True).mean() for _ in range(1000)]
            low, high = np.quantile(boot, [0.025, 0.975])
            status = "useful" if low > 0 and fold_wins >= 3 else "rejected" if high < 0 else "experimental"
            useful += status == "useful"
            rejected += status == "rejected"
            written += 1
            con.execute(
                "INSERT INTO stage21_factor_evaluation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                [
                    feature,
                    horizon,
                    sample.trade_date.min(),
                    sample.trade_date.max(),
                    len(sample),
                    folds,
                    float(np.mean(baseline_hits)),
                    float(np.mean(model_hits)),
                    float(delta.mean()),
                    float(low),
                    float(high),
                    float(np.nanmean(ics)),
                    fold_wins,
                    status,
                ],
            )
    return {
        "status": "research_only_no_promotion",
        "evaluations": written,
        "useful": useful,
        "rejected": rejected,
        "features": features,
    }
