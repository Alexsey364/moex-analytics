import duckdb

from moex_analytics.conditioned_stock_forecasting.core import HORIZONS, SECIDS
from moex_analytics.evidence_registry.core import BLOCKS, build_evidence_registry, evidence_strength


def test_evidence_strength_requires_positive_ci_stable_folds_and_history() -> None:
    strong = evidence_strength(gain=0.02, ci_low=0.01, folds=True, sample_n=100, fresh=True)
    weak = evidence_strength(gain=0.02, ci_low=-0.01, folds=True, sample_n=100, fresh=True)
    assert strong[0] == "STRONG_RESEARCH_EVIDENCE"
    assert weak[0] == "WEAK_RESEARCH_EVIDENCE"
    assert evidence_strength(gain=0.02, ci_low=0.01, folds=False, sample_n=100, fresh=True)[0] == "UNSTABLE"
    short = evidence_strength(gain=0.02, ci_low=0.01, folds=True, sample_n=20, fresh=True)
    assert short[0] == "INSUFFICIENT_HISTORY"
    assert (
        evidence_strength(gain=-0.01, ci_low=-0.02, folds=True, sample_n=100, fresh=True)[0] == "NO_EVIDENCE"
    )
    assert (
        evidence_strength(gain=0.01, ci_low=0.001, folds=True, sample_n=100, fresh=False)[0] == "NO_EVIDENCE"
    )
    moderate = evidence_strength(
        gain=0.01, ci_low=0.001, folds=True, sample_n=100, fresh=True, multiple_testing=False
    )
    assert moderate[0] == "MODERATE_RESEARCH_EVIDENCE"


def test_registry_contract_has_all_required_blocks() -> None:
    expected = {
        "baseline",
        "market_conditioned",
        "sector_conditioned",
        "ranking",
        "distribution",
        "analog",
        "news",
        "fundamental",
        "valuation",
        "risk",
        "portfolio_concentration",
        "live",
    }
    assert expected == set(BLOCKS)


def test_registry_builds_complete_immutable_matrix_and_audit() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE conditioned_stock_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("CREATE TABLE whole_market_tournament_runs(run_id VARCHAR,created_at TIMESTAMP)")
    con.execute("""CREATE TABLE whole_market_live_runs(run_id VARCHAR,created_at TIMESTAMP,cutoff DATE)""")
    con.execute("INSERT INTO conditioned_stock_runs VALUES ('conditioned','2026-01-01')")
    con.execute("INSERT INTO whole_market_tournament_runs VALUES ('tournament','2026-01-01')")
    con.execute("INSERT INTO whole_market_live_runs VALUES ('live','2026-01-01','2026-01-01')")
    con.execute(
        """CREATE TABLE conditioned_stock_scorecards(
        run_id VARCHAR,secid VARCHAR,horizon INTEGER,feature_block VARCHAR,observations BIGINT,
        baseline_mae DOUBLE,model_mae DOUBLE,improvement DOUBLE,ci_low DOUBLE,ci_high DOUBLE,
        fold_stable BOOLEAN,status VARCHAR)"""
    )
    conditioned = []
    for secid in SECIDS:
        for horizon in HORIZONS:
            conditioned.append(
                (
                    "conditioned",
                    secid,
                    horizon,
                    "issuer_sector",
                    100,
                    0.1,
                    0.08,
                    0.02,
                    0.01,
                    0.03,
                    True,
                    "experimental",
                )
            )
    con.executemany("INSERT INTO conditioned_stock_scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", conditioned)
    con.execute(
        """CREATE TABLE whole_market_tournament_entries(
        run_id VARCHAR,scope VARCHAR,instrument VARCHAR,horizon INTEGER,observations BIGINT,
        score DOUBLE,baseline_score DOUBLE,improvement DOUBLE,ci_low DOUBLE,ci_high DOUBLE,
        subperiod_stable BOOLEAN,regime_stable BOOLEAN,status VARCHAR)"""
    )
    con.execute(
        """INSERT INTO whole_market_tournament_entries VALUES
        ('tournament','fusion','TATNP',5,100,-.08,-.1,.02,.01,.03,TRUE,TRUE,'shadow_candidate')"""
    )
    con.execute(
        """CREATE TABLE live_stock_rank_forecasts(
        run_id VARCHAR,secid VARCHAR,horizon INTEGER)"""
    )
    con.executemany(
        "INSERT INTO live_stock_rank_forecasts VALUES ('live',?,?)",
        [(secid, horizon) for secid in SECIDS for horizon in HORIZONS],
    )
    result = build_evidence_registry(con)
    assert result["blocks"] == len(SECIDS) * len(HORIZONS) * len(BLOCKS)
    assert result["production_changes"] == 0
    assert build_evidence_registry(con)["idempotent"] is True
    assert con.execute("SELECT count(*) FROM evidence_decision_audit").fetchone()[0] == result["blocks"]
    shadow = con.execute(
        """SELECT evidence_status,decision_eligible FROM evidence_registry_blocks
        WHERE instrument='TATNP' AND horizon=5 AND block_type='analog'"""
    ).fetchone()
    assert shadow == ("MODERATE_RESEARCH_EVIDENCE", True)
