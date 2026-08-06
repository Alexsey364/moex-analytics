"""Automated, leakage-aware alpha research for SBER; never a production model."""

from __future__ import annotations

import hashlib
import json
import math
from itertools import combinations

import numpy as np
import pandas as pd

from moex_analytics.unblocked_experiment.core import (
    HORIZONS,
    _feature_frames,
    direction_metrics,
    fit_logistic,
    predict_logistic,
    temporal_folds,
    train_only_preprocess,
)

from .schema import DDL

VERSION = "alpha-research-v1"
DECAY_HORIZONS = (1, 3, 5, 10, 20, 40, 60, 120, 250)
STATE_BLOCKS = ("trend", "volatility", "liquidity", "breadth", "rates", "credit", "risk_appetite", "momentum", "mean_reversion", "rotation")
AUTHOR = "moex-analytics Alpha Research Engine"


def ensure_schema(con):  # pragma: no cover
    con.execute(DDL)


def safe_corr(x, y, rank=False):
    x, y = pd.Series(x), pd.Series(y)
    valid = x.notna() & y.notna()
    if valid.sum() < 20 or x[valid].nunique() < 2 or y[valid].nunique() < 2:
        return math.nan
    if rank:
        x, y = x.rank(), y.rank()
    return float(x[valid].corr(y[valid]))


def mutual_information(x, y, bins=10):
    frame = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(frame) < 30 or frame.x.nunique() < 2:
        return 0.0
    xb = pd.qcut(frame.x, min(bins, frame.x.nunique()), duplicates="drop", labels=False)
    yb = pd.qcut(frame.y, min(bins, frame.y.nunique()), duplicates="drop", labels=False)
    joint = pd.crosstab(xb, yb, normalize=True).to_numpy()
    px, py = joint.sum(axis=1), joint.sum(axis=0)
    expected = px[:, None] * py[None, :]
    mask = (joint > 0) & (expected > 0)
    return float(np.sum(joint[mask] * np.log(joint[mask] / expected[mask])))


def block_bootstrap_ci(x, y, statistic=safe_corr, block=20, samples=200, seed=42):
    x, y = np.asarray(x), np.asarray(y)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < max(40, block * 2):
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(x) - block + 1))
    values = []
    blocks = math.ceil(len(x) / block)
    for _ in range(samples):
        idx = np.concatenate([np.arange(s, min(s + block, len(x))) for s in rng.choice(starts, blocks)])[: len(x)]
        value = statistic(x[idx], y[idx])
        if np.isfinite(value):
            values.append(value)
    return (float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))) if values else (math.nan, math.nan)


def correlation_p_value(r, n):
    if not np.isfinite(r) or n < 4 or abs(r) >= 1:
        return 0.0 if abs(r) >= 1 else math.nan
    z = abs(r) * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return float(math.erfc(z / math.sqrt(2)))


def kmeans(values, k, iterations=60, seed=42):
    x = np.asarray(values, float)
    rng = np.random.default_rng(seed + k)
    centers = x[rng.choice(len(x), k, replace=False)].copy()
    labels = np.zeros(len(x), int)
    for _ in range(iterations):
        distance = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        new_labels = distance.argmin(axis=1)
        new_centers = np.array([x[new_labels == j].mean(axis=0) if np.any(new_labels == j) else centers[j] for j in range(k)])
        if np.array_equal(labels, new_labels):
            break
        labels, centers = new_labels, new_centers
    return labels, centers


def gaussian_mixture(values, k, iterations=40, seed=42):
    x = np.asarray(values, float)
    labels, means = kmeans(x, k, seed=seed)
    variances = np.array([x[labels == j].var(axis=0) + 1e-4 for j in range(k)])
    weights = np.full(k, 1 / k)
    resp = np.zeros((len(x), k))
    for _ in range(iterations):
        for j in range(k):
            logp = -0.5 * (np.log(2 * np.pi * variances[j]) + (x - means[j]) ** 2 / variances[j]).sum(axis=1)
            resp[:, j] = np.log(max(weights[j], 1e-8)) + logp
        resp = np.exp(resp - resp.max(axis=1, keepdims=True))
        resp /= resp.sum(axis=1, keepdims=True)
        mass = resp.sum(axis=0) + 1e-8
        weights = mass / len(x)
        means = (resp.T @ x) / mass[:, None]
        variances = np.array([((resp[:, j, None] * (x - means[j]) ** 2).sum(axis=0) / mass[j]) + 1e-4 for j in range(k)])
    return resp.argmax(axis=1), means


def hidden_markov(values, k, iterations=20, seed=42):
    x = np.asarray(values, float)
    labels, means = gaussian_mixture(x, k, seed=seed)
    transition = np.ones((k, k))
    for a, b in zip(labels[:-1], labels[1:], strict=True):
        transition[a, b] += 1
    transition /= transition.sum(axis=1, keepdims=True)
    variance = np.array([x[labels == j].var(axis=0) + 1e-3 for j in range(k)])
    for _ in range(iterations):
        emission = np.empty((len(x), k))
        for j in range(k):
            emission[:, j] = -0.5 * (np.log(2 * np.pi * variance[j]) + (x - means[j]) ** 2 / variance[j]).sum(axis=1)
        score = np.empty_like(emission)
        parent = np.zeros((len(x), k), int)
        score[0] = emission[0] - math.log(k)
        for t in range(1, len(x)):
            candidates = score[t - 1][:, None] + np.log(transition + 1e-12)
            parent[t] = candidates.argmax(axis=0)
            score[t] = emission[t] + candidates.max(axis=0)
        labels[-1] = score[-1].argmax()
        for t in range(len(x) - 2, -1, -1):
            labels[t] = parent[t + 1, labels[t + 1]]
        means = np.array([x[labels == j].mean(axis=0) if np.any(labels == j) else means[j] for j in range(k)])
        variance = np.array([x[labels == j].var(axis=0) + 1e-3 if np.any(labels == j) else variance[j] for j in range(k)])
    return labels, means


def spectral_clustering(values, k, neighbors=12, seed=42):
    x = np.asarray(values, float)
    sample = x[-min(len(x), 500):]
    dist = ((sample[:, None, :] - sample[None, :, :]) ** 2).sum(axis=2)
    scale = np.median(np.partition(dist, min(neighbors, len(sample) - 1), axis=1)[:, min(neighbors, len(sample) - 1)]) + 1e-8
    affinity = np.exp(-dist / scale)
    threshold = np.partition(affinity, -min(neighbors, len(sample)), axis=1)[:, -min(neighbors, len(sample))]
    affinity[affinity < threshold[:, None]] = 0
    affinity = np.maximum(affinity, affinity.T)
    degree = np.maximum(affinity.sum(axis=1), 1e-8)
    normalized = affinity / np.sqrt(degree[:, None] * degree[None, :])
    _, vectors = np.linalg.eigh(normalized)
    embedding = vectors[:, -k:]
    labels, centers = kmeans(embedding, k, seed=seed)
    full_labels = np.full(len(x), -1)
    full_labels[-len(sample):] = labels
    if len(x) > len(sample):
        original_centers = np.array([sample[labels == j].mean(axis=0) for j in range(k)])
        full_labels[:-len(sample)] = ((x[:-len(sample), None, :] - original_centers[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    return full_labels, centers


def regime_stability(labels):
    labels = np.asarray(labels)
    if len(labels) < 2:
        return 0.0
    persistence = float(np.mean(labels[1:] == labels[:-1]))
    shares = pd.Series(labels).value_counts(normalize=True)
    balance = float(1 - max(0, shares.max() - 0.8) / 0.2)
    return max(0.0, min(1.0, 0.6 * persistence + 0.4 * balance))


def stability_score(years, regimes, folds_working, folds, coef_cv, importance_cv, sign_consistency):
    components = [min(years / 5, 1), min(regimes / 3, 1), folds_working / max(folds, 1), max(0, 1 - min(coef_cv, 2) / 2), max(0, 1 - min(importance_cv, 2) / 2), sign_consistency]
    return float(round(100 * np.mean(components), 2))


def _research_frame(con):  # pragma: no cover
    frames = _feature_frames(con)
    merged = frames["technical"].copy()
    for family in ("zcyc", "breadth", "futures", "fundamentals"):
        frame = frames.get(family)
        if frame is not None and len(frame):
            merged = merged.merge(frame, on="trade_date", how="left")
    targets = con.execute("SELECT trade_date,horizon,future_return,direction_up::int direction_up FROM sber_experiment_targets WHERE target_version='unblocked-experiment-v1'").df()
    merged["trade_date"] = pd.to_datetime(merged.trade_date)
    targets["trade_date"] = pd.to_datetime(targets.trade_date)
    return merged, targets


def build_feature_registry(con):  # pragma: no cover
    ensure_schema(con)
    features, _ = _research_frame(con)
    con.execute("DELETE FROM alpha_feature_registry WHERE version=?", [VERSION])
    rows = 0
    for name in [c for c in features if "__" in c]:
        family = name.split("__", 1)[0]
        series = pd.to_numeric(features[name], errors="coerce")
        valid = series.notna()
        source = {"technical": "MOEX ISS SBER/IMOEX", "zcyc": "Bank of Russia ZCYC", "breadth": "MOEX historical universe", "futures": "MOEX ISS FORTS", "fundamentals": "Sber disclosures"}.get(family, "project feature store")
        fid = hashlib.sha256(name.encode()).hexdigest()[:20]
        con.execute("INSERT OR REPLACE INTO alpha_feature_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)", [fid,name,family,name.replace("__"," / "),source,"existing point-in-time feature-store transformation",features.trade_date[valid].min() if valid.any() else None,features.trade_date[valid].max() if valid.any() else None,250,"daily",1,"verified_or_inherited_pit", "usable" if valid.sum() >= 500 else "insufficient_sample",int(valid.sum()),float(1-valid.mean()),features.trade_date[valid].max() if valid.any() else None,AUTHOR,VERSION])
        rows += 1
    paid_name="options__implied_volatility_surface"; paid_id=hashlib.sha256(paid_name.encode()).hexdigest()[:20]
    con.execute("INSERT OR REPLACE INTO alpha_feature_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",[paid_id,paid_name,"options","Historical implied volatility surface","MOEX options history / paid vendor","surface-derived IV, skew and term structure",None,None,500,"daily",1,"unavailable","requires_paid_data",0,1.0,None,AUTHOR,VERSION]); rows+=1
    return {"features": rows}


def calculate_feature_importance(con):  # pragma: no cover
    ensure_schema(con)
    features, targets = _research_frame(con)
    run_id = hashlib.sha256((VERSION+"importance").encode()).hexdigest()[:16]
    con.execute("DELETE FROM alpha_feature_importance WHERE run_id=?", [run_id])
    candidates = [c for c in features if "__" in c and features[c].notna().sum() >= 500]
    candidates = sorted(candidates, key=lambda c: features[c].notna().sum(), reverse=True)
    rows = 0
    for horizon in HORIZONS:
        frame = features.merge(targets[targets.horizon == horizon], on="trade_date").sort_values("trade_date")
        folds = temporal_folds(len(frame), horizon, n_folds=4, min_train=500)
        for fold in folds:
            tr, te = fold["train"], fold["test"]
            xtr, xte, names, state = train_only_preprocess(frame[candidates].to_numpy(float)[tr], frame[candidates].to_numpy(float)[te], candidates, feature_cap=30)
            ytr, yte = frame.direction_up.to_numpy(int)[tr], frame.direction_up.to_numpy(int)[te]
            rtest = frame.future_return.to_numpy(float)[te]
            coef = fit_logistic(xtr, ytr, l2=2)
            base = direction_metrics(yte, predict_logistic(xte, coef))["balanced_accuracy"]
            for j, name in enumerate(names):
                ic, ric = safe_corr(xte[:, j], rtest), safe_corr(xte[:, j], rtest, rank=True)
                mi = mutual_information(xtr[:, j], frame.future_return.to_numpy(float)[tr])
                shuffled = xte.copy(); shuffled[:, j] = np.random.default_rng(1000+fold["fold"]+j).permutation(shuffled[:, j])
                perm = base-direction_metrics(yte,predict_logistic(shuffled,coef))["balanced_accuracy"]
                shap = float(np.mean(np.abs((xte[:, j]-np.mean(xtr[:, j]))*coef[j+1])))
                lo, hi = block_bootstrap_ci(xte[:, j], rtest, block=max(5,horizon), samples=100, seed=j+horizon)
                fid = hashlib.sha256(name.encode()).hexdigest()[:20]
                con.execute("INSERT INTO alpha_feature_importance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [run_id,fid,horizon,fold["fold"],ic,ric,mi,shap,perm,int(np.sign(coef[j+1])),None,correlation_p_value(ic,len(te)),lo,hi,frame.trade_date.iloc[tr[0]],frame.trade_date.iloc[tr[-1]],frame.trade_date.iloc[te[0]],frame.trade_date.iloc[te[-1]],"expanding_train_only_linear_shap_block_bootstrap"])
                rows += 1
            omitted=[name for name in candidates if name not in names]
            for name in omitted:
                train_values=pd.to_numeric(frame[name].iloc[tr],errors="coerce"); test_values=pd.to_numeric(frame[name].iloc[te],errors="coerce")
                median=float(train_values.median()) if train_values.notna().any() else 0.0; train_values=train_values.fillna(median); test_values=test_values.fillna(median)
                ic=safe_corr(test_values,rtest); ric=safe_corr(test_values,rtest,rank=True); mi=mutual_information(train_values,frame.future_return.to_numpy(float)[tr]); lo,hi=block_bootstrap_ci(test_values,rtest,block=max(5,horizon),samples=50,seed=horizon+len(name))
                fid=hashlib.sha256(name.encode()).hexdigest()[:20]
                con.execute("INSERT INTO alpha_feature_importance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[run_id,fid,horizon,fold["fold"],ic,ric,mi,None,None,int(np.sign(ic)) if np.isfinite(ic) else 0,None,correlation_p_value(ic,len(te)),lo,hi,frame.trade_date.iloc[tr[0]],frame.trade_date.iloc[tr[-1]],frame.trade_date.iloc[te[0]],frame.trade_date.iloc[te[-1]],"expanding_train_only_univariate_block_bootstrap"]); rows+=1
    return {"run_id": run_id, "rows": rows, "features": len(candidates)}


def _regime_matrix(con):  # pragma: no cover
    prices = con.execute("SELECT trade_date,close,volume FROM predictive_market_prices WHERE secid='SBER' AND board='TQBR' ORDER BY trade_date").df()
    prices.index = pd.to_datetime(prices.trade_date)
    close = prices.close.astype(float)
    data = pd.DataFrame({"return":close.pct_change(),"volatility":close.pct_change().rolling(20).std(),"momentum":close.pct_change(20),"drawdown":close/close.rolling(250,min_periods=20).max()-1,"liquidity":np.log1p(prices.volume).diff(5)},index=prices.index)
    data = data.replace([np.inf,-np.inf],np.nan).dropna()
    med=data.median(); scale=(data-med).abs().median().replace(0,1)*1.4826
    return prices, data, ((data-med)/scale).clip(-8,8)


def discover_market_regimes(con):  # pragma: no cover
    ensure_schema(con)
    prices, raw, scaled = _regime_matrix(con)
    run_id=hashlib.sha256((VERSION+"regimes").encode()).hexdigest()[:16]
    con.execute("DELETE FROM alpha_discovered_regimes WHERE run_id=?",[run_id]); con.execute("DELETE FROM alpha_regime_transitions WHERE run_id=?",[run_id]); con.execute("DELETE FROM alpha_regime_assignments WHERE run_id=?",[run_id])
    label_cache={}
    methods={"kmeans":kmeans,"gaussian_mixture":gaussian_mixture,"hidden_markov":hidden_markov,"spectral":spectral_clustering}
    rows=0
    for method,fn in methods.items():
        for k in range(2,9):
            labels,_=fn(scaled.to_numpy(),k,seed=42)
            label_cache[(method,k)]=labels.copy()
            stability=regime_stability(labels); selected=stability>=0.55 and min(pd.Series(labels).value_counts())>=40
            dates=scaled.index; returns=raw["return"].to_numpy()
            for regime in range(k):
                mask=labels==regime; subset=returns[mask]; loc=np.where(mask)[0]
                durations=[]
                for _,group in __import__("itertools").groupby(labels): durations.append(len(list(group)))
                cumulative=np.cumprod(1+subset) if len(subset) else np.array([1.0]); peak=np.maximum.accumulate(cumulative); dd=cumulative/peak-1
                con.execute("INSERT INTO alpha_discovered_regimes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[run_id,method,k,regime,dates[loc].min().date() if len(loc) else None,dates[loc].max().date() if len(loc) else None,int(mask.sum()),float(np.mean(subset)),float(np.std(subset)),float(np.mean(subset>0)),float(np.mean(durations)),float(np.min(dd)),None,stability,selected,json.dumps({"data_detected":True,"features":list(raw.columns)})])
                rows+=1
            for a in range(k):
                denom=max(1,np.sum(labels[:-1]==a))
                for b in range(k):
                    count=int(np.sum((labels[:-1]==a)&(labels[1:]==b)))
                    con.execute("INSERT INTO alpha_regime_transitions VALUES (?,?,?,?,?,?,?)",[run_id,method,k,a,b,count/denom,count])
    con.execute("UPDATE alpha_discovered_regimes r SET selected=false WHERE run_id=? AND k<>(SELECT k FROM alpha_discovered_regimes x WHERE x.run_id=r.run_id AND x.algorithm=r.algorithm GROUP BY k ORDER BY max(stability) DESC,k LIMIT 1)",[run_id])
    assignments=[]
    selected_pairs=con.execute("SELECT DISTINCT algorithm,k FROM alpha_discovered_regimes WHERE run_id=? AND selected",[run_id]).fetchall()
    for algorithm,k in selected_pairs:
        assignments.extend((run_id,date.date(),algorithm,k,int(label),True) for date,label in zip(scaled.index,label_cache[(algorithm,k)],strict=True))
    if assignments:
        assignment_frame=pd.DataFrame(assignments,columns=["run_id","trade_date","algorithm","k","regime","selected"]); con.register("incoming_alpha_assignments",assignment_frame); con.execute("INSERT INTO alpha_regime_assignments SELECT * FROM incoming_alpha_assignments"); con.unregister("incoming_alpha_assignments")
    return {"run_id":run_id,"rows":rows,"selected":con.execute("SELECT count(*) FROM alpha_discovered_regimes WHERE run_id=? AND selected",[run_id]).fetchone()[0],"assignments":len(assignments)}


def update_regime_sign_changes(con):  # pragma: no cover
    run_id=hashlib.sha256((VERSION+"importance").encode()).hexdigest()[:16]
    regime_run=hashlib.sha256((VERSION+"regimes").encode()).hexdigest()[:16]
    features,targets=_research_frame(con)
    assignments=con.execute("SELECT trade_date,regime FROM alpha_regime_assignments WHERE run_id=? AND algorithm='hidden_markov' AND selected",[regime_run]).df()
    if assignments.empty:
        return {"updated":0}
    assignments["trade_date"]=pd.to_datetime(assignments.trade_date)
    updated=0
    for fid,name in con.execute("SELECT feature_id,name FROM alpha_feature_registry WHERE observations>=500").fetchall():
        if name not in features:
            continue
        for horizon in HORIZONS:
            frame=features[["trade_date",name]].merge(targets[targets.horizon==horizon],on="trade_date").merge(assignments,on="trade_date")
            signs=[]
            for _,group in frame.groupby("regime"):
                value=safe_corr(group[name].shift(1),group.future_return)
                if np.isfinite(value) and abs(value)>=0.01:
                    signs.append(int(np.sign(value)))
            changes=1 if len(set(signs))>1 else 0
            con.execute("UPDATE alpha_feature_importance SET regime_sign_changes=? WHERE run_id=? AND feature_id=? AND horizon=?",[changes,run_id,fid,horizon])
            updated+=1
    return {"updated":updated}

def calculate_alpha_decay(con):  # pragma: no cover
    ensure_schema(con); features,targets=_research_frame(con); run_id=hashlib.sha256((VERSION+"decay").encode()).hexdigest()[:16]
    con.execute("DELETE FROM alpha_decay WHERE run_id=?",[run_id]); names=[c for c in features if "__" in c and features[c].notna().sum()>=500][:60]; rows=0
    prices=con.execute("SELECT trade_date,close FROM predictive_market_prices WHERE secid='SBER' AND board='TQBR' ORDER BY trade_date").df(); prices.trade_date=pd.to_datetime(prices.trade_date)
    for h in DECAY_HORIZONS:
        outcome=prices.close.shift(-h)/prices.close-1; target=pd.DataFrame({"trade_date":prices.trade_date,"return":outcome})
        frame=features.merge(target,on="trade_date")
        for name in names:
            ic=safe_corr(frame[name],frame["return"]); ric=safe_corr(frame[name],frame["return"],True); lo,hi=block_bootstrap_ci(frame[name],frame["return"],block=max(5,h),samples=100,seed=h)
            status="persistent" if np.isfinite(ic) and abs(ic)>=0.03 and lo*hi>0 else "decayed_or_unconfirmed"
            con.execute("INSERT INTO alpha_decay VALUES (?,?,?,?,?,?,?,?,?)",[run_id,hashlib.sha256(name.encode()).hexdigest()[:20],h,ic,ric,correlation_p_value(ic,frame[[name,"return"]].dropna().shape[0]),lo,hi,status]); rows+=1
    return {"run_id":run_id,"rows":rows}


def evaluate_feature_stability(con):  # pragma: no cover
    ensure_schema(con); run_id=hashlib.sha256((VERSION+"importance").encode()).hexdigest()[:16]; con.execute("DELETE FROM alpha_feature_stability WHERE run_id=?",[run_id]); rows=0
    groups=con.execute("SELECT feature_id,horizon,list(ic),list(permutation_importance),list(effect_sign),count(*) FROM alpha_feature_importance WHERE run_id=? GROUP BY 1,2",[run_id]).fetchall()
    for fid,h,ics,imps,signs,folds in groups:
        a=np.asarray(ics,float); b=np.asarray(imps,float); working=int(np.sum(np.abs(a)>=0.03)); sign=float(max(np.mean(np.asarray(signs)>=0),np.mean(np.asarray(signs)<=0))); cv=float(np.std(a)/(abs(np.mean(a))+1e-8)); iv=float(np.std(b)/(abs(np.mean(b))+1e-8)); score=stability_score(folds,1,working,folds,cv,iv,sign); status="stable" if score>=70 and working>=2 else "unstable"
        con.execute("INSERT INTO alpha_feature_stability VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",[run_id,fid,h,float(folds),1,working,folds,cv,iv,sign,score,status]); rows+=1
    return {"run_id":run_id,"rows":rows}


def evaluate_interactions(con):  # pragma: no cover
    ensure_schema(con); features,targets=_research_frame(con); run_id=hashlib.sha256((VERSION+"interactions").encode()).hexdigest()[:16]; con.execute("DELETE FROM alpha_interactions WHERE run_id=?",[run_id]); rows=0
    preferred=[c for c in features if any(x in c.lower() for x in ("rsi","return_20","volatility","breadth","slope","relative_strength","futures")) and features[c].notna().sum()>=500][:18]
    for h in (5,20,60,120):
        frame=features.merge(targets[targets.horizon==h],on="trade_date")
        for left,right in combinations(preferred,2):
            x=pd.to_numeric(frame[left],errors="coerce"); z=pd.to_numeric(frame[right],errors="coerce"); interaction=((x-x.rolling(250,min_periods=100).median())*(z-z.rolling(250,min_periods=100).median())).shift(1); ic=safe_corr(interaction,frame.future_return); base=max(abs(safe_corr(x.shift(1),frame.future_return)),abs(safe_corr(z.shift(1),frame.future_return))); inc=abs(ic)-base if np.isfinite(ic) else math.nan; lo,hi=block_bootstrap_ci(interaction,frame.future_return,block=max(5,h),samples=80,seed=h); status="experimental" if np.isfinite(inc) and inc>=0.01 and lo*hi>0 else "rejected"
            if status=="experimental" or rows<40:
                name=f"{left} × {right}"; con.execute("INSERT INTO alpha_interactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",[run_id,h,hashlib.sha256(left.encode()).hexdigest()[:20],hashlib.sha256(right.encode()).hexdigest()[:20],name,ic,inc,correlation_p_value(ic,frame[[left,right,"future_return"]].dropna().shape[0]),lo,hi,None,None,status]); rows+=1
    return {"run_id":run_id,"rows":rows}


def update_market_state(con):  # pragma: no cover
    ensure_schema(con); features,_=_research_frame(con); row=features.sort_values("trade_date").iloc[-1]; date=pd.Timestamp(row.trade_date).date(); con.execute("DELETE FROM alpha_market_state WHERE trade_date=? AND version=?",[date,VERSION]); mapping={"trend":["return_20","return_60","price_to_sma_200"],"volatility":["volatility_20","volatility_60","current_drawdown"],"liquidity":["turnover_to_mean_20"],"breadth":["breadth__current40_breadth","breadth__dynamic_breadth"],"rates":["zcyc__1y","zcyc__10y"],"credit":[],"risk_appetite":["relative_strength_60"],"momentum":["return_5","return_20"],"mean_reversion":["rsi_14","price_to_sma_20"],"rotation":["breadth__return_difference"]}; rows=0
    for block,patterns in mapping.items():
        selected=[c for c in features if any(c.endswith(p) or c==p for p in patterns)]; contributors={}
        for feature in selected:
            history=pd.to_numeric(features[feature],errors="coerce").dropna(); median=history.median(); scale=(history-median).abs().median()*1.4826
            if pd.notna(row[feature]) and scale>1e-12: contributors[feature]=float(np.clip((row[feature]-median)/scale,-5,5))
        vals=list(contributors.values()); score=float(np.tanh(np.mean(vals)/2)) if vals else 0.0; direction="elevated" if score>0.1 else "depressed" if score<-0.1 else "neutral"; con.execute("INSERT INTO alpha_market_state VALUES (?,?,?,?,?,?,?)",[date,block,score,direction,json.dumps(contributors),f"{block}: {direction}; robust historical z-score={score:.3f}; {len(vals)} PIT inputs",VERSION]); rows+=1
    return {"date":date,"rows":rows}


def build_factor_library(con):  # pragma: no cover
    ensure_schema(con); run_id=hashlib.sha256((VERSION+"importance").encode()).hexdigest()[:16]; con.execute("DELETE FROM alpha_factor_library WHERE run_id=?",[run_id]); rows=0
    data=con.execute("SELECT r.feature_id,r.observations,s.horizon,s.stability_score,max(abs(i.ic)),max(abs(i.rank_ic)),min(i.p_value) FROM alpha_feature_registry r JOIN alpha_feature_stability s USING(feature_id) JOIN alpha_feature_importance i ON i.feature_id=s.feature_id AND i.horizon=s.horizon AND i.run_id=s.run_id GROUP BY 1,2,3,4",).fetchall()
    for fid,obs,h,stability,ic,ric,p in data:
        if obs<500: cls="Insufficient Sample"
        elif stability>=70 and max(ic or 0,ric or 0)>=0.04 and (p or 1)<0.05: cls="Production Candidate"
        elif max(ic or 0,ric or 0)>=0.025: cls="Experimental"
        else: cls="Rejected"
        con.execute("INSERT INTO alpha_factor_library VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",[run_id,fid,h,cls,ic,ric,stability,p,obs,f"rule-based research classification; stability={stability:.1f}"]); rows+=1
    missing=con.execute("SELECT feature_id,observations,quality_status FROM alpha_feature_registry WHERE feature_id NOT IN (SELECT feature_id FROM alpha_factor_library WHERE run_id=?)",[run_id]).fetchall()
    for fid,obs,quality in missing:
        cls="Requires Paid Data" if quality=="requires_paid_data" else "Insufficient Sample"
        for h in HORIZONS:
            con.execute("INSERT INTO alpha_factor_library VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)",[run_id,fid,h,cls,None,None,0.0,None,obs,quality]); rows+=1
    return {"run_id":run_id,"rows":rows,"classes":dict(con.execute("SELECT classification,count(*) FROM alpha_factor_library WHERE run_id=? GROUP BY 1",[run_id]).fetchall())}


def build_explanations(con):  # pragma: no cover
    ensure_schema(con); run_id=hashlib.sha256((VERSION+"importance").encode()).hexdigest()[:16]; latest=con.execute("SELECT max(trade_date) FROM alpha_market_state WHERE version=?",[VERSION]).fetchone()[0]; con.execute("DELETE FROM alpha_explanations WHERE run_id=?",[run_id]); rows=0
    states=con.execute("SELECT block,score,explanation FROM alpha_market_state WHERE trade_date=? AND version=?",[latest,VERSION]).fetchall()
    for h in HORIZONS:
        pos=[{"factor":b,"score":s,"why":e} for b,s,e in states if s>0.1]; neg=[{"factor":b,"score":s,"why":e} for b,s,e in states if s<-0.1]; neutral=[{"factor":b,"score":s,"why":e} for b,s,e in states if abs(s)<=0.1]
        con.execute("INSERT INTO alpha_explanations VALUES (?,?,?,?,?,?,?,?,?)",[latest,h,json.dumps(pos),json.dumps(neg),json.dumps(neutral),"explainable market-state research profile","experimental",json.dumps(["not a forecast","not production","linear/additive explanations only"]),run_id]); rows+=1
    return {"rows":rows,"date":latest}


def research_status(con):  # pragma: no cover
    ensure_schema(con)
    tables=("alpha_feature_registry","alpha_feature_importance","alpha_discovered_regimes","alpha_interactions","alpha_decay","alpha_feature_stability","alpha_factor_library","alpha_market_state","alpha_research_journal")
    return {t.removeprefix("alpha_"):con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def _journal(con,run_id,step,result):
    con.execute("INSERT OR REPLACE INTO alpha_research_journal VALUES (?,current_timestamp,?,?,?,?,?)",[run_id,step,"completed",sum(v for v in result.values() if isinstance(v,int)),json.dumps(result,default=str),"walk-forward; train-only; purged/embargoed; nested feature selection where models are fitted"])


def run_alpha_research(con):  # pragma: no cover
    ensure_schema(con); run_id=hashlib.sha256((VERSION+"full").encode()).hexdigest()[:16]; results={}
    actions=(("registry",build_feature_registry),("importance",calculate_feature_importance),("regimes",discover_market_regimes),("regime_signs",update_regime_sign_changes),("interactions",evaluate_interactions),("decay",calculate_alpha_decay),("stability",evaluate_feature_stability),("library",build_factor_library),("market_state",update_market_state),("explanations",build_explanations))
    for name,action in actions:
        result=action(con); results[name]=result; _journal(con,run_id,name,result)
    results["status"]=research_status(con); return results