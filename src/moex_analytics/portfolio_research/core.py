"""Instrument-agnostic portfolio research, risk and shadow tracking."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import numpy as np
import yaml

from moex_analytics.canonical import build_canonical
from moex_analytics.config import PROJECT_ROOT
from moex_analytics.database import insert_daily_prices, upsert_segments
from moex_analytics.features import calculate_all as calculate_features
from moex_analytics.forward_returns import calculate_all as calculate_forward_returns
from moex_analytics.moex_client import MoexClient
from moex_analytics.returns import calculate_all as calculate_returns

from .schema import DDL

VERSION="portfolio-research-v1"
HORIZONS=(1,5,20,60,120,250)
SCENARIOS={"rate_plus_2pp":{"rates":2},"rate_minus_2pp":{"rates":-2},"rate_minus_4pp":{"rates":-4},"ruble_plus_10":{"fx":.10},"ruble_minus_10":{"fx":-.10},"oil_plus_15":{"oil":.15},"oil_minus_15":{"oil":-.15},"imoex_minus_15":{"market":-.15},"liquidity_stress":{"liquidity":-.25},"dividend_cut":{"dividend":-.30},"sector_shock":{"sector":-.20},"combined_stress":{"market":-.15,"liquidity":-.25,"dividend":-.30}}


def ensure_schema(con):  # pragma: no cover
    con.execute(DDL)
def load_config(): return yaml.safe_load((PROJECT_ROOT/"config/portfolio_instruments.yaml").read_text(encoding="utf-8"))
def all_specs():
    cfg=load_config(); return cfg["instruments"]+cfg.get("controls",[])+cfg.get("supports",[])

def normalize_weights(values):
    x=np.maximum(np.asarray(values,float),0); return x/x.sum() if x.sum() else np.full(len(x),1/max(len(x),1))
def annualized_volatility(returns): return float(np.nanstd(returns,ddof=1)*np.sqrt(252))
def downside_volatility(returns):
    x=np.asarray(returns,float); return float(np.sqrt(np.nanmean(np.minimum(x,0)**2))*np.sqrt(252))
def max_drawdown(returns):
    wealth=np.cumprod(1+np.nan_to_num(returns)); peak=np.maximum.accumulate(wealth); return float(np.min(wealth/peak-1)) if len(wealth) else math.nan
def risk_contributions(cov,weights):
    w=normalize_weights(weights); marginal=np.asarray(cov)@w; variance=float(w@marginal)
    return w*marginal/variance if variance>0 else np.zeros_like(w)
def inverse_volatility_weights(cov): return normalize_weights(1/np.sqrt(np.maximum(np.diag(cov),1e-12)))
def minimum_variance_weights(cov):
    inv=np.linalg.pinv(np.asarray(cov,float)); ones=np.ones(len(inv)); return normalize_weights(inv@ones)
def maximum_diversification_weights(cov):
    inv=np.linalg.pinv(np.asarray(cov,float)); vols=np.sqrt(np.maximum(np.diag(cov),1e-12)); return normalize_weights(inv@vols)
def risk_parity_weights(cov,iterations=500):
    cov=np.asarray(cov,float); w=np.full(len(cov),1/len(cov))
    for _ in range(iterations):
        rc=risk_contributions(cov,w); target=np.full(len(w),1/len(w)); w=normalize_weights(w*np.sqrt(target/np.maximum(rc,1e-8)))
    return w
def hierarchical_risk_parity(cov):
    cov=np.asarray(cov,float); n=len(cov); order=list(np.argsort(np.diag(cov))); w=np.ones(n)
    clusters=[order]
    while clusters:
        cluster=clusters.pop(0)
        if len(cluster)<=1: continue
        mid=len(cluster)//2; left,right=cluster[:mid],cluster[mid:]
        lv=float(np.mean(cov[np.ix_(left,left)])); rv=float(np.mean(cov[np.ix_(right,right)])); alpha=rv/max(lv+rv,1e-12)
        w[left]*=alpha; w[right]*=1-alpha; clusters.extend([left,right])
    return normalize_weights(w)
def lot_round(values,prices,lots,cash_limit):
    desired=np.asarray(values)*cash_limit; units=np.floor(desired/(np.asarray(prices)*np.asarray(lots)))*np.asarray(lots); return units.astype(int)
def transaction_cost(old_weights,new_weights,value,bps=10): return float(np.abs(np.asarray(new_weights)-np.asarray(old_weights)).sum()*value*bps/10000)
def point_in_time_spread(preferred,ordinary): return preferred/ordinary-1

def validate_identity_transition(old_secid,new_secid,conversion_ratio,legal_evidence):
    errors=[]
    if not old_secid or not new_secid: errors.append("both SECID required")
    if conversion_ratio is None or conversion_ratio<=0: errors.append("positive conversion ratio required")
    if not legal_evidence: errors.append("legal continuity evidence required")
    return not errors,errors

def purged_walk_forward(n,horizon,min_train=250,folds=4):
    available=n-min_train-2*horizon
    if available<folds*20: return []
    size=available//folds; result=[]
    for i in range(folds):
        test_start=min_train+horizon+i*size; test_end=min(n,test_start+size); train_end=test_start-horizon
        result.append((np.arange(train_end),np.arange(test_start,test_end)))
    return result

def apply_constraints(weights,max_weight=1.0,frozen=None):
    w=normalize_weights(weights); frozen=frozen or {}
    for idx,value in frozen.items(): w[idx]=value
    free=[i for i in range(len(w)) if i not in frozen]
    remaining=max(0,1-sum(frozen.values()))
    if free:
        values=np.minimum(w[free],max_weight); w[free]=normalize_weights(values)*remaining
    return w
def discover_portfolio_instruments(con,client=None):  # pragma: no cover
    ensure_schema(con); client=client or MoexClient(); cfg=load_config(); written=0
    for spec in all_specs():
        secid=spec["secid"]; payload=client.get_json(f"securities/{secid}.json",{"iss.meta":"off"}); description={r[0]:r[2] for r in payload["description"]["data"]}; cols=payload["boards"]["columns"]; boards=[dict(zip(cols,r,strict=True)) for r in payload["boards"]["data"]]; primary=next((b for b in boards if b.get("is_primary")==1),boards[0]); history=[b for b in boards if b.get("history_from")]
        con.execute("INSERT OR REPLACE INTO portfolio_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",[secid,description.get("ISIN"),description.get("NAME"),description.get("SHORTNAME") or description.get("NAME"),spec.get("share_class"),description.get("TYPE"),primary.get("boardid"),description.get("LOTSIZE"),spec.get("sector"),spec.get("sector"),description.get("LISTLEVEL"),description.get("FACEUNIT") or "RUB",min((b["history_from"] for b in history),default=None),None,"issuer_specific",spec["fundamental_family"],json.dumps([]),json.dumps({"boards":len(history),"iss":True}),"research_only"])
        con.execute("DELETE FROM instrument_identities WHERE canonical_secid=?",[secid]); con.execute("INSERT INTO instrument_identities VALUES (?,?,?,?,?,?,?,?,?,?)",[secid,secid,description.get("ISIN"),description.get("REGNUMBER"),None,1.0,min((b["history_from"] for b in history),default=None),None,"official_iss_confirmed",f"https://iss.moex.com/iss/securities/{secid}.json"])
        con.execute("INSERT OR REPLACE INTO instrument_analysis_profiles VALUES (?,?,?,?,?,?,?,?,?)",[secid,spec["fundamental_family"],spec.get("ordinary_pair"),cfg["benchmark"],spec.get("sector"),json.dumps(HORIZONS),True,True,VERSION])
        segments=[]
        for i,b in enumerate(history): segments.append({"canonical_secid":secid,"source_secid":secid,"engine":b["engine"],"market":b["market"],"board":b["boardid"],"date_from":b["history_from"],"date_to":b.get("history_till") or date.today(),"priority":100 if b.get("is_primary")==1 else 20-i,"is_primary":bool(b.get("is_primary")),"notes":"official ISS historical board"})
        upsert_segments(con,segments); con.execute("INSERT OR REPLACE INTO instrument_source_availability VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",[secid,"prices","discoverable",min((b["history_from"] for b in history),default=None),None,0,"MOEX ISS","unknown","pending_download",f"{len(history)} historical boards"]); written+=1
    # X5/FIVE continuity requires a documented ratio and legal evidence.
    con.execute("INSERT OR REPLACE INTO instrument_lifecycles VALUES (?,?,?,?,?,?,?,?,?,?)",["X5",date(2025,1,9),"predecessor_review","FIVE","X5",None,"unverified","MOEX ISS and issuer documents required","requires_manual_review","No mechanical GDR/common-share splice"])
    return {"instruments":written}

def download_portfolio_history(con,client=None):  # pragma: no cover
    ensure_schema(con); client=client or MoexClient(); inserted=0; dividends=0
    segments=con.execute("SELECT canonical_secid,source_secid,engine,market,board,date_from,date_to,priority,is_primary FROM instrument_history_segments WHERE canonical_secid IN (SELECT secid FROM portfolio_instruments)").fetchall()
    for canonical,source,engine,market,board,from_date,to_date,_priority,_primary in segments:
        instrument={"canonical_secid":canonical,"source_secid":source,"engine":engine,"market":market,"board":board}
        rows=[]
        for payload,_,url in client.history_pages(instrument,str(from_date),str(min(to_date,date.today()))): rows.extend(client.normalize_history(payload,source,board,url))
        inserted+=insert_daily_prices(con,rows)
    for secid, in con.execute("SELECT secid FROM portfolio_instruments").fetchall():
        rows=client.dividends(secid)
        for r in rows: con.execute("INSERT OR REPLACE INTO dividends VALUES (?,?,?,?,?,?,?,?,?)",list(r.values())); dividends+=1
    build_canonical(con); calculate_returns(con); calculate_features(con); calculate_forward_returns(con)
    for secid, in con.execute("SELECT secid FROM portfolio_instruments").fetchall():
        state=con.execute("SELECT min(trade_date),max(trade_date),count(*) FROM canonical_daily_prices WHERE canonical_secid=?",[secid]).fetchone(); con.execute("INSERT OR REPLACE INTO instrument_source_availability VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",[secid,"prices","downloaded",state[0],state[1],state[2],"MOEX ISS","fresh" if state[1] and (date.today()-state[1]).days<10 else "stale","validated" if state[2]>250 else "insufficient",None])
    return {"price_rows_inserted":inserted,"dividends":dividends}

def build_portfolio_total_returns(con):  # pragma: no cover
    build_canonical(con); rows=calculate_returns(con); return {"return_rows":rows}

def audit_preferred_share_rules(con):  # pragma: no cover
    rules={"SBERP":("SBER","same issuer; preferred dividend rights require charter review"),"LSNGP":("LSNG","charter-based preferred dividend formula; manual legal review required"),"TATNP":("TATN","preferred rights per charter; issuer policy risk"),"TRNFP":(None,"no exchange-traded ordinary pair confirmed in configured universe")}; rows=0
    for pref,(ordinary,formula) in rules.items(): con.execute("INSERT OR REPLACE INTO preferred_share_rules VALUES (?,?,?,?,?,?,?,?,?,current_timestamp)",[pref,ordinary,"preferred class; no automatic equivalence",formula,"review_from_market_data","different voting rights","requires_manual_review","issuer charter and MOEX ISS","requires_manual_review"]); rows+=1
    for pref,ordinary in (("SBERP","SBER"),("TATNP","TATN"),("LSNGP","LSNG")):
        con.execute("DELETE FROM preferred_share_spreads WHERE preferred_secid=?",[pref]); con.execute("INSERT INTO preferred_share_spreads SELECT p.trade_date,?,?,p.close,o.close,p.close/o.close-1,(p.close/o.close)/(lag(p.close/o.close,20) over(order by p.trade_date))-1,NULL FROM canonical_daily_prices p JOIN canonical_daily_prices o USING(trade_date) WHERE p.canonical_secid=? AND o.canonical_secid=?",[pref,ordinary,pref,ordinary])
    return {"rules":rows,"spreads":con.execute("SELECT count(*) FROM preferred_share_spreads").fetchone()[0]}

def _corr_p(r,n): return float(math.erfc(abs(r)*math.sqrt(max(n-2,1)/max(1-r*r,1e-12))/math.sqrt(2))) if np.isfinite(r) else math.nan
def run_portfolio_alpha_research(con):  # pragma: no cover
    ensure_schema(con); run=hashlib.sha256((VERSION+"alpha").encode()).hexdigest()[:16]; con.execute("DELETE FROM instrument_alpha_results WHERE run_id=?",[run]); features=("return_5","return_20","return_60","return_120","return_250","volatility_20","volatility_60","drawdown_60","relative_imoex_20"); rows=0
    for secid, in con.execute("SELECT secid FROM portfolio_instruments").fetchall():
        price=con.execute("SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? ORDER BY trade_date",[secid]).df(); bench=con.execute("SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid='IMOEX' ORDER BY trade_date").df()
        if len(price)<300: continue
        price["ret"]=price.close.pct_change(fill_method=None); price["return_5"]=price.close.pct_change(5,fill_method=None); price["return_20"]=price.close.pct_change(20,fill_method=None); price["return_60"]=price.close.pct_change(60,fill_method=None); price["return_120"]=price.close.pct_change(120,fill_method=None); price["return_250"]=price.close.pct_change(250,fill_method=None); price["volatility_20"]=price.ret.rolling(20).std(); price["volatility_60"]=price.ret.rolling(60).std(); price["drawdown_60"]=price.close/price.close.rolling(60).max()-1
        if len(bench): bench["b20"]=bench.close.pct_change(20); price=price.merge(bench[["trade_date","b20"]],on="trade_date",how="left"); price["relative_imoex_20"]=price.return_20-price.b20
        for h in HORIZONS:
            price["target"]=price.close.shift(-h)/price.close-1
            for feature in features:
                valid=price[[feature,"target"]].dropna(); n=len(valid)
                if n<250: continue
                splits=np.array_split(np.arange(n)[250:],4); ics=[]
                for test in splits:
                    if len(test)<20: continue
                    ic=float(valid.iloc[test][feature].corr(valid.iloc[test].target)); ics.append(ic)
                ic=float(valid[feature].corr(valid.target)); ric=float(valid[feature].rank().corr(valid.target.rank())); consistency=max(np.mean(np.asarray(ics)>=0),np.mean(np.asarray(ics)<=0)) if ics else 0; score=100*(.4*min(n/1500,1)+.3*consistency+.3*min(abs(ic)/.08,1)); status="instrument_specific_candidate" if score>=70 and abs(ic)>=.04 and consistency>=.75 else "experimental" if abs(ic)>=.025 else "rejected"
                con.execute("INSERT INTO instrument_alpha_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[run,secid,h,feature,ic,ric,_corr_p(ic,n),None,None,int(sum(np.sign(ics)==np.sign(ic))),len(ics),score,"not_yet_regime_validated",status]); rows+=1
    build_cross_instrument_features(con,run); return {"run_id":run,"rows":rows}
def build_cross_instrument_features(con,run_id=None):  # pragma: no cover
    run_id=run_id or hashlib.sha256((VERSION+"alpha").encode()).hexdigest()[:16]; con.execute("DELETE FROM portfolio_cross_instrument_factors WHERE run_id=?",[run_id]); rows=0
    data=con.execute("SELECT feature,horizon,list(secid),list(ic),list(status) FROM instrument_alpha_results WHERE run_id=? GROUP BY 1,2",[run_id]).fetchall()
    for feature,h,ids,ics,statuses in data:
        a=np.asarray(ics,float); working=int(sum(s!="rejected" for s in statuses)); sign=max(np.mean(a>=0),np.mean(a<=0)); status="universal_factor_candidate" if working>=6 and sign>=.75 else "sector_factor_candidate" if working>=3 else "instrument_specific_candidate" if working else "rejected"; con.execute("INSERT INTO portfolio_cross_instrument_factors VALUES (?,?,?,?,?,?,?,?,?,?,?)",[run_id,feature,h,working,0,float(sign),float(np.mean(a)),float(np.min(a)),float(np.max(a)),status,json.dumps({"instruments":ids})]); rows+=1
    return {"rows":rows}

def _positions():
    path=PROJECT_ROOT/"config/portfolio_positions.local.yaml"; return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"cash":0,"positions":[]}
def build_portfolio_risk(con):  # pragma: no cover
    ensure_schema(con); cfg=_positions(); positions=cfg.get("positions",[]); ids=[p["secid"] for p in positions]
    if not ids: return {"status":"local_positions_missing","positions":0}
    latest={s:c for s,c in con.execute("SELECT canonical_secid,arg_max(close,trade_date) FROM canonical_daily_prices WHERE canonical_secid IN (SELECT unnest(?)) GROUP BY 1",[ids]).fetchall()}; values=np.array([p["quantity"]*latest.get(p["secid"],0) for p in positions]); cash=float(cfg.get("cash",0)); total=values.sum()+cash; weights=values/max(total,1e-12); sid=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()[:20]; con.execute("INSERT OR REPLACE INTO portfolio_snapshots VALUES (?,current_date,current_timestamp,?,?,?,?)",[sid,hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),total,cash,"research_only"])
    for p,v,w in zip(positions,values,weights,strict=True): con.execute("INSERT OR REPLACE INTO portfolio_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",[sid,p["secid"],p["quantity"],p.get("average_price"),latest.get(p["secid"]),v,w,p.get("target_weight"),p.get("max_weight"),p.get("can_add",False),p.get("horizon",250)])
    matrix=con.execute("SELECT trade_date,canonical_secid,total_return FROM daily_returns WHERE canonical_secid IN (SELECT unnest(?)) QUALIFY row_number() over(partition by trade_date,canonical_secid order by calculation_version desc)=1",[ids]).df().pivot(index="trade_date",columns="canonical_secid",values="total_return").dropna(); matrix=matrix[ids]; cov=matrix.cov().to_numpy()*252; port=matrix.to_numpy()@weights; metrics={"volatility":annualized_volatility(port),"downside_volatility":downside_volatility(port),"max_drawdown":max_drawdown(port),"beta":math.nan,"issuer_concentration":float(np.sum(weights**2))}
    for name,value in metrics.items(): con.execute("INSERT OR REPLACE INTO portfolio_risk_metrics VALUES (?,?,?,?,?,?)",[sid,name,value,"historical PIT total returns","research",json.dumps({})])
    methods={"current":weights,"equal_weight":normalize_weights(np.ones(len(ids))),"inverse_volatility":inverse_volatility_weights(cov),"risk_parity":risk_parity_weights(cov),"hierarchical_risk_parity":hierarchical_risk_parity(cov),"minimum_variance":minimum_variance_weights(cov),"maximum_diversification":maximum_diversification_weights(cov)}
    for name,w in methods.items(): vol=float(np.sqrt(w@cov@w)); ratio=float((w@np.sqrt(np.diag(cov)))/max(vol,1e-12)); con.execute("INSERT OR REPLACE INTO portfolio_rebalancing_experiments VALUES (?,?,?,?,?,?,?,?,?)",[sid,name,json.dumps(dict(zip(ids,w,strict=True))),vol,ratio,float(np.abs(w-weights).sum()),transaction_cost(weights,w,total),"no_short; lot rounding pending execution",True])
    return {"snapshot_id":sid,"positions":len(ids),"total_value":total,"volatility":metrics["volatility"]}
def build_portfolio_dividend_calendar(con):  # pragma: no cover
    ensure_schema(con); con.execute("DELETE FROM portfolio_dividend_calendar"); rows=con.execute("INSERT INTO portfolio_dividend_calendar SELECT canonical_secid,'historical',declared_date,NULL,registry_close_date,payment_date,dividend_per_share,currency,'official_history',source FROM dividends WHERE canonical_secid IN (SELECT secid FROM portfolio_instruments) RETURNING *").fetchall(); return {"rows":len(rows)}
def calculate_portfolio_scenarios(con):  # pragma: no cover
    latest=con.execute("SELECT snapshot_id FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1").fetchone()
    if not latest: return {"status":"no_snapshot","rows":0}
    sid=latest[0]; positions=con.execute("SELECT p.secid,p.weight,i.sector FROM portfolio_positions p JOIN portfolio_instruments i USING(secid) WHERE snapshot_id=?",[sid]).fetchall(); rows=0
    sensitivity={"bank":{"rates":-.03,"market":1.0},"oil_gas":{"oil":.55,"fx":-.35,"market":.8},"utility_grid":{"rates":-.12,"market":.5},"telecom":{"rates":-.08,"market":.6},"consumer_retail":{"fx":-.3,"market":.7},"fertilizers_chemicals":{"fx":-.4,"market":.7,"oil":.1},"exchange_infrastructure":{"rates":.08,"market":.9}}
    for name,shock in SCENARIOS.items():
        impacts=[]
        for secid,w,sector in positions: impacts.append((secid,w*sum(sensitivity.get(sector,{}).get(k,0)*v for k,v in shock.items())))
        total=sum(v for _,v in impacts); winners=[s for s,v in impacts if v>0]; losers=[s for s,v in impacts if v<0]; con.execute("INSERT OR REPLACE INTO portfolio_scenarios VALUES (?,?,?,?,?,?,?)",[sid,name,total,"low_to_medium","historical/economic sensitivity scenario; not forecast",json.dumps(winners),json.dumps(losers)]); rows+=1
    return {"snapshot_id":sid,"rows":rows}
def save_portfolio_live_shadow(con):  # pragma: no cover
    ensure_schema(con); inserted=0
    for secid, in con.execute("SELECT secid FROM portfolio_instruments").fetchall():
        row=con.execute("SELECT trade_date,close FROM canonical_daily_prices WHERE canonical_secid=? ORDER BY trade_date DESC LIMIT 1",[secid]).fetchone()
        if not row: continue
        candidates=con.execute("SELECT feature,horizon,status FROM instrument_alpha_results WHERE secid=? AND status<>'rejected' ORDER BY stability_score DESC LIMIT 10",[secid]).fetchall(); input_hash=hashlib.sha256(json.dumps([secid,str(row[0]),row[1],candidates],default=str).encode()).hexdigest(); shadow=hashlib.sha256(f"{secid}|{row[0]}|{VERSION}".encode()).hexdigest()[:24]; before=con.execute("SELECT count(*) FROM portfolio_live_shadow WHERE shadow_id=?",[shadow]).fetchone()[0]; con.execute("INSERT OR IGNORE INTO portfolio_live_shadow VALUES (?,current_timestamp,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[shadow,row[0],secid,row[1],"data_discovered_unassigned",json.dumps(candidates),"research_only_no_trade_recommendation",json.dumps({"method":"historical"}),json.dumps({}),json.dumps({}),"medium",VERSION,input_hash,True,"research_only"]); inserted+=not before
    return {"inserted":inserted,"total":con.execute("SELECT count(*) FROM portfolio_live_shadow").fetchone()[0]}
def audit_external_projects(con):  # pragma: no cover
    ensure_schema(con); projects=[("okama","okama","https://github.com/mbk-dev/okama","mbk-dev","MIT","use_as_dependency","wealth/risk/frontier methodology; reproduce before adoption"),("okama-macro","okama-macro","https://github.com/mbk-dev/okama-macro","mbk-dev","MIT","research_further","macro data adapters"),("openbb","OpenBB","https://github.com/OpenBB-finance/OpenBB","OpenBB-finance","AGPL-3.0-only","adapt_architectural_pattern","provider architecture only; do not copy AGPL code"),("openbb-forecast","openbb-forecast","https://github.com/OpenBB-finance/openbb-forecast","OpenBB-finance","AGPL-3.0","reference_only","forecast methods require independent OOS reproduction"),("russian-markets-lab","russian-markets-lab","https://github.com/sergey-lastochkin/russian-markets-lab","sergey-lastochkin","NO LICENSE","reference_only","no copying rights; provenance ideas only"),("island-model","Moex / Island Model","https://github.com/artemleonich/Moex","artemleonich","NO LICENSE","reference_only","no copying; published results untrusted until reproduced"),("backtrader-moexalgo","backtrader_moexalgo","https://github.com/WISEPLAT/backtrader_moexalgo","WISEPLAT","MIT","adapt_architectural_pattern","feed/strategy separation; no live trading"),("portfolio-allocation","portfolio-allocation","https://github.com/fertkir/portfolio-allocation","fertkir","GPL-3.0","reference_only","archived; reimplement methodology only"),("moexalgo","moexalgo","https://github.com/moexalgo/moexalgo","moexalgo","Apache-2.0 claimed README","use_as_dependency","official ecosystem but paid data may be required")]
    for pid,name,url,owner,license,recommend,note in projects: con.execute("INSERT OR REPLACE INTO external_project_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",[pid,name,url,owner,license,hashlib.sha256(license.encode()).hexdigest(),None,None,"active_or_recent" if pid not in {"openbb-forecast","portfolio-allocation"} else "stale_or_archived","Python",json.dumps([]),json.dumps(["MOEX"]),json.dumps(["public APIs"]),json.dumps([]),"repository-specific","provider/data separation","requires reproduction", "permissive_with_attribution" if license in {"MIT","Apache-2.0 claimed README"} else "copyleft_or_no_permission","medium",recommend,note])
    return {"projects":len(projects)}
def portfolio_status(con):  # pragma: no cover
    ensure_schema(con); return {"instruments":con.execute("SELECT count(*) FROM portfolio_instruments").fetchone()[0],"price_rows":con.execute("SELECT count(*) FROM canonical_daily_prices WHERE canonical_secid IN (SELECT secid FROM portfolio_instruments)").fetchone()[0],"alpha":con.execute("SELECT count(*) FROM instrument_alpha_results").fetchone()[0],"cross_factors":con.execute("SELECT count(*) FROM portfolio_cross_instrument_factors").fetchone()[0],"shadows":con.execute("SELECT count(*) FROM portfolio_live_shadow").fetchone()[0],"external_projects":con.execute("SELECT count(*) FROM external_project_audit").fetchone()[0]}
def update_user_portfolio_research(con):  # pragma: no cover
    ensure_schema(con); run=hashlib.sha256((VERSION+str(date.today())).encode()).hexdigest()[:20]; before=portfolio_status(con); results={}; actions=(("discover",discover_portfolio_instruments),("download",download_portfolio_history),("returns",build_portfolio_total_returns),("preferred",audit_preferred_share_rules),("alpha",run_portfolio_alpha_research),("risk",build_portfolio_risk),("dividends",build_portfolio_dividend_calendar),("scenarios",calculate_portfolio_scenarios),("shadow",save_portfolio_live_shadow),("audit",audit_external_projects))
    for name,action in actions: results[name]=action(con)
    after=portfolio_status(con); no_change=before==after; con.execute("INSERT OR REPLACE INTO portfolio_pipeline_runs VALUES (?,current_timestamp,current_timestamp,?,?,?,?,?)",[run,"completed",json.dumps(results,default=str),sum(v for v in after.values()),no_change,VERSION]); results["status"]=after; results["no_change"]=no_change; return results
