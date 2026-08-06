"""Tests for leakage-safe Alpha Research Engine primitives."""

import duckdb
import numpy as np

from moex_analytics.alpha_research.core import (
    DECAY_HORIZONS,
    STATE_BLOCKS,
    block_bootstrap_ci,
    correlation_p_value,
    gaussian_mixture,
    hidden_markov,
    kmeans,
    mutual_information,
    regime_stability,
    safe_corr,
    spectral_clustering,
    stability_score,
)
from moex_analytics.alpha_research.schema import DDL


def clustered(seed=4):
    rng=np.random.default_rng(seed)
    return np.r_[rng.normal(-2,.2,(60,2)),rng.normal(2,.2,(60,2))]


def test_safe_ic_and_rank_ic():
    x=np.arange(100); y=x*2
    assert safe_corr(x,y)>0.99
    assert safe_corr(x,y,rank=True)>0.99
    assert np.isnan(safe_corr(np.ones(100),y))


def test_mutual_information_detects_dependency():
    x=np.linspace(-2,2,300)
    assert mutual_information(x,x**2)>.5
    assert mutual_information(np.ones(300),x)==0


def test_block_bootstrap_is_deterministic_and_ordered():
    x=np.arange(200,dtype=float); y=x+np.sin(x)
    first=block_bootstrap_ci(x,y,samples=30)
    assert first==block_bootstrap_ci(x,y,samples=30)
    assert first[0]<=first[1]


def test_correlation_p_value():
    assert correlation_p_value(.8,100)<.01
    assert np.isnan(correlation_p_value(np.nan,100))


def test_kmeans_finds_clusters():
    labels,centers=kmeans(clustered(),2)
    assert len(np.unique(labels))==2
    assert centers.shape==(2,2)


def test_gaussian_mixture_finds_clusters():
    labels,means=gaussian_mixture(clustered(),2,iterations=8)
    assert len(np.unique(labels))==2
    assert means.shape==(2,2)


def test_hidden_markov_is_temporally_defined():
    labels,means=hidden_markov(clustered(),2,iterations=3)
    assert len(labels)==120
    assert means.shape==(2,2)


def test_spectral_clustering():
    labels,_=spectral_clustering(clustered(),2,neighbors=8)
    assert len(labels)==120
    assert set(labels)=={0,1}


def test_regime_stability_bounds():
    assert 0<=regime_stability([0,0,1,1,1])<=1
    assert regime_stability([1])==0


def test_stability_score_bounds_and_rewards_consistency():
    weak=stability_score(1,1,1,4,2,2,.5)
    strong=stability_score(6,4,4,4,0,0,1)
    assert 0<=weak<strong<=100


def test_required_decay_horizons_and_market_blocks():
    assert DECAY_HORIZONS==(1,3,5,10,20,40,60,120,250)
    assert {"trend","volatility","liquidity","breadth","rates","credit","risk_appetite","momentum","mean_reversion","rotation"}==set(STATE_BLOCKS)


def test_schema_contains_research_tables_without_decision_tables():
    con=duckdb.connect(":memory:")
    con.execute(DDL)
    tables={x[0] for x in con.execute("select table_name from information_schema.tables").fetchall()}
    assert "alpha_feature_registry" in tables
    assert "alpha_factor_library" in tables
    assert "alpha_explanations" in tables
    assert not any("decision" in table for table in tables)