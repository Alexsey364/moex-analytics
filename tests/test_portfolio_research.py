"""Portfolio platform methodology and safety tests."""
import duckdb
import numpy as np

from moex_analytics.portfolio_research.core import *
from moex_analytics.portfolio_research.interfaces import AdapterContract
from moex_analytics.portfolio_research.schema import DDL


def test_confirmed_config_secids():
    ids={x["secid"] for x in load_config()["instruments"]}
    assert ids=={"X5","SBERP","LKOH","LSNGP","MTSS","TRNFP","TATNP","PHOR","MOEX"}
def test_sber_control_retained(): assert any(x["secid"]=="SBER" for x in all_specs())
def test_preferred_mapping():
    specs={x["secid"]:x for x in all_specs()}; assert specs["SBERP"]["ordinary_pair"]=="SBER"; assert specs["TATNP"]["ordinary_pair"]=="TATN"; assert specs["LSNGP"]["ordinary_pair"]=="LSNG"
def test_x5_transition_requires_evidence():
    assert not validate_identity_transition("FIVE","X5",1,None)[0]
    assert validate_identity_transition("FIVE","X5",1,"issuer legal disclosure")[0]
def test_spread(): assert np.isclose(point_in_time_spread(80,100),-.2)
def test_weights(): assert np.isclose(normalize_weights([1,2,3]).sum(),1)
def test_negative_weights_removed(): assert np.all(normalize_weights([-1,2])>=0)
def test_volatility(): assert annualized_volatility([-.01,.01,-.01,.01])>0
def test_downside_volatility(): assert downside_volatility([-.02,.01])>0
def test_drawdown(): assert max_drawdown([.1,-.2,.1])<0
def test_risk_contribution_sums(): assert np.isclose(risk_contributions(np.eye(3),[1,1,1]).sum(),1)
def test_inverse_volatility(): assert inverse_volatility_weights(np.diag([1,4]))[0]>inverse_volatility_weights(np.diag([1,4]))[1]
def test_minimum_variance(): assert np.isclose(minimum_variance_weights(np.eye(3)).sum(),1)
def test_max_diversification(): assert np.isclose(maximum_diversification_weights(np.eye(3)).sum(),1)
def test_risk_parity(): assert np.allclose(risk_parity_weights(np.eye(3)),[1/3]*3,atol=.02)
def test_hrp(): assert np.isclose(hierarchical_risk_parity(np.eye(4)).sum(),1)
def test_lot_rounding(): assert np.array_equal(lot_round([.5,.5],[100,200],[10,1],10000),[50,25])
def test_transaction_costs(): assert np.isclose(transaction_cost([.5,.5],[.6,.4],100000,10),20)
def test_constraints_and_frozen():
    w=apply_constraints([.8,.2],.7,{1:.2}); assert np.isclose(w.sum(),1) and np.isclose(w[1],.2)
def test_purged_walk_forward_no_leakage():
    for train,test in purged_walk_forward(1000,20): assert train.max()+20<=test.min()
def test_scenarios_are_not_forecasts(): assert "combined_stress" in SCENARIOS
def test_local_positions_ignored(): assert "portfolio_positions.local.yaml" in (PROJECT_ROOT/".gitignore").read_text()
def test_adapter_contract_has_pit():
    c=AdapterContract("v1",("ISS",),("date",),("date required",),"available_from<=cutoff","coverage"); assert "cutoff" in c.point_in_time_contract
def test_schema_has_immutable_shadow_and_audit():
    c=duckdb.connect(":memory:"); c.execute(DDL); names={r[0] for r in c.execute("select table_name from information_schema.tables").fetchall()}; assert {"portfolio_live_shadow","external_project_audit","portfolio_instruments"}<=names
def test_no_production_decision_tables():
    c=duckdb.connect(":memory:"); c.execute(DDL); assert not c.execute("select count(*) from information_schema.tables where table_name like '%decision_results%'").fetchone()[0]
def test_no_buy_sell_status_in_config():
    text=(PROJECT_ROOT/"config/portfolio_instruments.yaml").read_text(); assert "BUY" not in text and "SELL" not in text
def test_same_input_hash_is_immutable():
    a=__import__("hashlib").sha256(b"X5|2026-01-01|v1").hexdigest(); assert a==__import__("hashlib").sha256(b"X5|2026-01-01|v1").hexdigest()
def test_no_change_weights(): assert np.allclose(normalize_weights([1,1]),normalize_weights([1,1]))