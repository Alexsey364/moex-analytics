"""Research-only dashboard for the Alpha Research Engine."""

import streamlit as st

from moex_analytics.database import connection


def _table(sql, params=None):
    with connection(read_only=True) as con:
        return con.execute(sql, params or []).df()


def render_registry():
    st.header("Feature Registry")
    st.caption("Point-in-time research catalog; no production eligibility is implied.")
    st.dataframe(_table("SELECT name,family,source,effective_from,effective_to,observations,missingness,pit_status,quality_status,version FROM alpha_feature_registry ORDER BY family,name"),use_container_width=True)


def render_importance():
    st.header("Feature Importance")
    st.dataframe(_table("SELECT r.name,i.horizon,avg(i.ic) ic,avg(i.rank_ic) rank_ic,avg(i.mutual_information) mutual_information,avg(i.linear_shap) linear_shap,avg(i.permutation_importance) permutation_importance,min(i.p_value) p_value FROM alpha_feature_importance i JOIN alpha_feature_registry r USING(feature_id) GROUP BY 1,2 ORDER BY abs(avg(i.ic)) DESC"),use_container_width=True)


def render_stability():
    st.header("Feature Stability")
    st.dataframe(_table("SELECT r.name,s.* EXCLUDE(feature_id,run_id) FROM alpha_feature_stability s JOIN alpha_feature_registry r USING(feature_id) ORDER BY stability_score DESC"),use_container_width=True)


def render_market_state():
    st.header("Market State")
    state=_table("SELECT trade_date,block,score,direction,explanation FROM alpha_market_state QUALIFY trade_date=max(trade_date) over() ORDER BY block")
    for row in state.itertuples():
        st.metric(row.block,f"{row.score:.2f}",row.direction)
        st.caption(row.explanation)


def render_regimes():
    st.header("Regime Discovery")
    st.caption("Data-discovered HMM, Gaussian mixture, KMeans and spectral regimes (k=2..8).")
    st.dataframe(_table("SELECT algorithm,k,regime,date_from,date_to,observations,mean_return,volatility,up_probability,mean_duration,max_drawdown,stability,selected FROM alpha_discovered_regimes ORDER BY selected DESC,stability DESC"),use_container_width=True)


def render_decay():
    st.header("Alpha Decay")
    st.dataframe(_table("SELECT r.name,d.horizon,d.ic,d.rank_ic,d.ci_low,d.ci_high,d.p_value,d.status FROM alpha_decay d JOIN alpha_feature_registry r USING(feature_id) ORDER BY r.name,d.horizon"),use_container_width=True)


def render_interactions():
    st.header("Interaction Matrix")
    st.dataframe(_table("SELECT horizon,interaction_name,ic,incremental_ic,ci_low,ci_high,p_value,status FROM alpha_interactions ORDER BY status,abs(incremental_ic) DESC"),use_container_width=True)


def render_candidates():
    st.header("Production Candidates")
    st.warning("Research classification only; this page does not promote factors into production.")
    st.dataframe(_table("SELECT r.name,f.horizon,f.classification,f.best_ic,f.best_rank_ic,f.stability_score,f.p_value,f.observations,f.reason FROM alpha_factor_library f JOIN alpha_feature_registry r USING(feature_id) ORDER BY classification,stability_score DESC"),use_container_width=True)


def render_journal():
    st.header("Research Journal")
    st.dataframe(_table("SELECT created_at,step,status,rows_written,summary_json,methodology FROM alpha_research_journal ORDER BY created_at DESC"),use_container_width=True)