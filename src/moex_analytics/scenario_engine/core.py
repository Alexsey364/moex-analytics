"""Stage 54: turn actual independent analog episodes into descriptive scenario trees."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .schema import DDL

VERSION = "historical-scenario-tree-v2-component-gaps"
WINDOWS = (20, 60, 120, 250)
HORIZONS = (5, 20, 60, 120, 250)
MIN_EPISODES = 5


def ensure_schema(con: Any) -> None:
    con.execute(DDL)


def classify_scenario(path: np.ndarray) -> str:
    """Fixed outcome description; never fitted to forecast performance."""
    terminal, low, high = float(path[-1]), float(path.min()), float(path.max())
    if low <= -.05 and terminal > 0:
        return "dip_then_recover"
    if terminal >= .03 and low > -.05:
        return "growth_without_deep_drawdown"
    if terminal <= -.03 and high < .05:
        return "continued_decline"
    if abs(terminal) < .03 and high - low < .10:
        return "sideways"
    return "volatile_mixed"


def medoid_date(paths: dict[pd.Timestamp, np.ndarray]) -> pd.Timestamp | None:
    if not paths:
        return None
    dates = list(paths)
    matrix = np.vstack([paths[date] for date in dates])
    distances = np.abs(matrix[:, None, :] - matrix[None, :, :]).mean(axis=2).sum(axis=1)
    return dates[int(np.argmin(distances))]


def _insert(con: Any, table: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    name = f"_{table}"
    con.register(name, frame)
    columns = ",".join(frame.columns)
    con.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {name}")
    con.unregister(name)


def _source_runs(con: Any) -> tuple[Any, ...]:
    analog = con.execute("SELECT run_id,cutoff FROM analog_search_runs_v3 WHERE status='completed' "
                         "ORDER BY finished_at DESC LIMIT 1").fetchone()
    trajectory = con.execute("SELECT run_id FROM analog_trajectory_runs WHERE status LIKE 'completed%' "
                             "ORDER BY finished_at DESC LIMIT 1").fetchone()
    event = con.execute("SELECT run_id FROM event_analog_runs WHERE status LIKE 'completed%' "
                        "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if not analog or not trajectory or not event:
        raise ValueError("completed Stage 44-46 runs are required")
    return analog[0], trajectory[0], event[0], analog[1]


def _matches(con: Any, analog_run: str, run_id: str) -> pd.DataFrame:
    source = con.execute(
        "SELECT secid,method,analog_date,path_window,distance,similarity_score,"
        "regime_agreement,event_state_agreement,feature_coverage,independent,"
        "why_similar_json,why_different_json "
        "FROM historical_analogs_v3 WHERE run_id=? AND analog_type='issuer' AND independent",
        [analog_run],
    ).df()
    rows = []
    for (secid, method, date), group in source.groupby(["secid", "method", "analog_date"]):
        distances = {int(row.path_window): float(row.distance) for row in group.itertuples()}
        coverage = float(group.feature_coverage.mean())
        feature_keys: set[str] = set()
        for payload in [*group.why_similar_json, *group.why_different_json]:
            if payload:
                feature_keys.update(json.loads(payload))
        component_keys = {
            "breadth": {"breadth_balance", "dispersion"},
            "fx": {"usd_change", "cny_change"},
            "rates": {"rusfar_change"},
            "oil": {"oil_change"},
            "rvi": {"rvi_change"},
            "rgbi": {"rgbi_change"},
            "sector": {"sector_return", "sector_relative"},
        }
        gaps = [name for name, keys in component_keys.items() if not feature_keys.intersection(keys)]
        combined = float(np.mean(list(distances.values())))
        applicability = "high" if coverage >= .85 and len(distances) >= 3 and len(gaps) <= 2 else (
            "medium" if coverage >= .65 and len(distances) >= 2 and len(gaps) <= 5 else "low"
        )
        rows.append([run_id, secid, method, date, distances.get(20), distances.get(60),
            distances.get(120), combined, float(group.similarity_score.mean()),
            bool(group.regime_agreement.fillna(False).all()),
            bool(group.event_state_agreement.fillna(False).all()), coverage, True,
            applicability, json.dumps(gaps), True])
    return pd.DataFrame(rows, columns=("run_id", "secid", "method", "analog_date",
        "short_distance", "medium_distance", "long_distance", "combined_distance",
        "similarity_score", "regime_agreement", "event_agreement", "feature_coverage",
        "independent", "applicability", "gaps_json", "immutable"))


def _price_series(con: Any, secid: str) -> pd.Series:
    frame = con.execute("SELECT trade_date,close FROM canonical_daily_prices "
                        "WHERE canonical_secid=? AND close>0 ORDER BY trade_date", [secid]).df()
    return pd.Series(frame.close.to_numpy(float), index=pd.to_datetime(frame.trade_date))


def _prehistory(con: Any, matches: pd.DataFrame, run_id: str) -> pd.DataFrame:
    rows = []
    top = matches.sort_values("combined_distance").groupby(["secid", "method"]).head(10)
    market = _price_series(con, "IMOEX")
    for secid, group in top.groupby("secid"):
        stock = _price_series(con, secid)
        for match in group.itertuples():
            for series_type, series in (("issuer", stock), ("market", market)):
                eligible = series.loc[:pd.Timestamp(match.analog_date)]
                for window in WINDOWS:
                    path = eligible.tail(window + 1)
                    if len(path) < window + 1:
                        continue
                    normalized = path / path.iloc[-1] * 100
                    for session, (source_date, value) in zip(range(-window, 1), normalized.items(),
                                                              strict=True):
                        rows.append([run_id, secid, match.method, match.analog_date, series_type,
                            window, session, source_date, float(value), True, True])
    return pd.DataFrame(rows, columns=("run_id", "secid", "method", "analog_date",
        "series_type", "path_window", "relative_session", "source_trade_date", "normalized_value",
        "observed", "immutable"))


def _episodes(con: Any, trajectory_run: str, event_run: str, matches: pd.DataFrame,
              run_id: str) -> tuple[pd.DataFrame, dict[tuple, np.ndarray], dict[tuple, list[Any]]]:
    trajectory = con.execute("SELECT secid,method,analog_date,forward_session,forward_return,"
                             "source_trade_date FROM analog_forward_trajectories WHERE run_id=?",
                             [trajectory_run]).df()
    profiles = con.execute("SELECT secid,method,analog_date,event_family,event_type "
                           "FROM analog_event_profiles WHERE run_id=?", [event_run]).df()
    event_map = {}
    for key, group in profiles.groupby(["secid", "method", "analog_date"]):
        families = [str(value) for value in group.event_family.dropna()]
        types = [str(value) for value in group.event_type.dropna()]
        event_map[key] = (families[0] if families else None,
                          any("systemic" in value.lower() or "shock" in value.lower()
                              for value in families + types))
    match_map = {(row.secid, row.method, pd.Timestamp(row.analog_date)):
                 (row.regime_agreement, row.event_agreement, row.applicability)
                 for row in matches.itertuples()}
    rows, paths, path_dates = [], {}, {}
    for (secid, method, date), group in trajectory.groupby(["secid", "method", "analog_date"]):
        key = (secid, method, pd.Timestamp(date))
        if key not in match_map:
            continue
        group = group.sort_values("forward_session")
        for horizon in HORIZONS:
            observed = group[group.forward_session <= horizon]
            if len(observed) < horizon:
                continue
            values = observed.forward_return.to_numpy(float)[:horizon]
            scenario = classify_scenario(values)
            event_family, systemic = event_map.get(key, (None, False))
            regime_ok, event_ok, _ = match_map[key]
            rows.append([run_id, secid, method, date, horizon, scenario, float(values[-1]),
                float(values.min()), float(values.max()), event_family, systemic, regime_ok,
                event_ok, True])
            paths[(secid, method, horizon, scenario, pd.Timestamp(date))] = values
            path_dates[(secid, method, horizon, scenario, pd.Timestamp(date))] = (
                observed.source_trade_date.tolist()[:horizon]
            )
    columns = ("run_id", "secid", "method", "analog_date", "horizon", "scenario",
        "terminal_return", "max_adverse", "max_favorable", "event_family", "systemic_shock",
        "regime_agreement", "event_agreement", "immutable")
    return pd.DataFrame(rows, columns=columns), paths, path_dates


def _summaries(episodes: pd.DataFrame, paths: dict, path_dates: dict, matches: pd.DataFrame,
               run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows, representative_rows = [], []
    applicability = matches.set_index(["secid", "method", "analog_date"]).applicability.to_dict()
    subset_masks = {
        "all_analogs": lambda frame: pd.Series(True, index=frame.index),
        "same_regime": lambda frame: frame.regime_agreement.fillna(False),
        "same_event_class": lambda frame: frame.event_agreement.fillna(False),
        "excluding_systemic_shocks": lambda frame: ~frame.systemic_shock.fillna(False),
    }
    for (secid, method, horizon), base in episodes.groupby(["secid", "method", "horizon"]):
        for subset, mask_function in subset_masks.items():
            selected = base[mask_function(base)]
            total = len(selected)
            for scenario, group in selected.groupby("scenario"):
                episode_paths = {(pd.Timestamp(row.analog_date)):
                    paths[(secid, method, horizon, scenario, pd.Timestamp(row.analog_date))]
                    for row in group.itertuples()}
                medoid = medoid_date(episode_paths)
                values = group.terminal_return
                levels = [applicability.get((secid, method, pd.Timestamp(date)), "low")
                          for date in group.analog_date]
                app = "high" if levels.count("high") >= MIN_EPISODES else (
                    "medium" if total >= MIN_EPISODES else "low"
                )
                status = "ready" if total >= MIN_EPISODES else "insufficient_data"
                summary_rows.append([run_id, secid, method, horizon, subset, scenario, len(group),
                    len(group) / total if total else None, float(values.median()),
                    float(values.quantile(.10)), float(values.quantile(.25)),
                    float(values.quantile(.75)), float(values.quantile(.90)),
                    float(group.max_adverse.median()), medoid, app, status,
                    "historical frequency; not calibrated probability", True])
                if medoid is not None:
                    medoid_path = episode_paths[medoid]
                    dates = path_dates[(secid, method, horizon, scenario, medoid)]
                    for session, (value, source_date) in enumerate(zip(medoid_path, dates, strict=True), 1):
                        representative_rows.append([run_id, secid, method, horizon, subset, scenario,
                            medoid, session, float(100 * (1 + value)), source_date, True, True])
    summary_columns = ("run_id", "secid", "method", "horizon", "subset", "scenario",
        "episodes", "historical_frequency", "median_return", "q10", "q25", "q75", "q90",
        "median_adverse", "medoid_analog_date", "applicability", "status", "reason", "immutable")
    path_columns = ("run_id", "secid", "method", "horizon", "subset", "scenario",
        "medoid_analog_date", "forward_session", "normalized_value", "source_trade_date",
        "actual_historical_path", "immutable")
    return pd.DataFrame(summary_rows, columns=summary_columns), pd.DataFrame(
        representative_rows, columns=path_columns
    )


def run_scenario_research(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    analog_run, trajectory_run, event_run, cutoff = _source_runs(con)
    run_id = hashlib.sha256(
        f"{VERSION}|{analog_run}|{trajectory_run}|{event_run}|{cutoff}".encode()
    ).hexdigest()[:20]
    cached = con.execute("SELECT status,match_rows,prehistory_rows,episode_rows,summary_rows "
                         "FROM scenario_research_runs WHERE run_id=?", [run_id]).fetchone()
    if cached and cached[0] == "completed":
        return {"run_id": run_id, "status": "completed", "matches": cached[1],
                "prehistory": cached[2], "episodes": cached[3], "summaries": cached[4],
                "cached": True}
    con.execute("INSERT OR REPLACE INTO scenario_research_runs "
        "(run_id,analog_run_id,trajectory_run_id,event_run_id,cutoff,started_at,status,"
        "match_rows,prehistory_rows,episode_rows,summary_rows,details_json,immutable) "
        "VALUES (?,?,?,?,?,current_timestamp,'running',0,0,0,0,?,true)",
        [run_id, analog_run, trajectory_run, event_run, cutoff,
         json.dumps({"synthetic_paths": False, "probability_published": False})])
    try:
        matches = _matches(con, analog_run, run_id)
        prehistory = _prehistory(con, matches, run_id)
        episodes, paths, path_dates = _episodes(
            con, trajectory_run, event_run, matches, run_id
        )
        summaries, representatives = _summaries(episodes, paths, path_dates, matches, run_id)
        current_rows = []
        contexts = con.execute("SELECT secid,novelty_status FROM current_event_contexts "
                               "WHERE run_id=?", [event_run]).fetchall()
        novelty = dict(contexts)
        all_summary = summaries[summaries.subset == "all_analogs"]
        filtered = summaries[summaries.subset == "excluding_systemic_shocks"]
        for (secid, horizon), group in all_summary.groupby(["secid", "horizon"]):
            ready = group[group.status == "ready"]
            if ready.empty:
                current_rows.append([run_id, cutoff, secid, int(horizon), None,
                    int(group.scenario.nunique()), int(group.episodes.sum()), "low",
                    novelty.get(secid, "unknown"), None, None, None, None,
                    "filtered historical comparison", False, "insufficient_data",
                    "fewer than five independent scenario episodes", True])
                continue
            leading = ready.sort_values("historical_frequency", ascending=False).iloc[0]
            comparable = filtered[(filtered.secid == secid) & (filtered.horizon == horizon) &
                                  (filtered.scenario == leading.scenario)]
            filtered_median = float(comparable.iloc[0].median_return) if not comparable.empty else None
            current_rows.append([run_id, cutoff, secid, int(horizon), leading.scenario,
                int(ready.scenario.nunique()), int(ready.episodes.sum()), leading.applicability,
                novelty.get(secid, "unknown"), float(leading.median_return), float(leading.q10),
                float(leading.q90), filtered_median, "filtered historical comparison", False,
                "research_only", "historical scenarios are descriptive, not probabilities", True])
        current = pd.DataFrame(current_rows, columns=("run_id", "cutoff", "secid", "horizon",
            "leading_scenario", "scenarios", "independent_episodes", "applicability",
            "event_novelty", "median_return", "q10", "q90", "filtered_median_return",
            "filtered_label", "probability_allowed", "status", "reason", "immutable"))
        for table, frame in (("scenario_multiscale_matches", matches),
                             ("scenario_prehistory_points", prehistory),
                             ("scenario_episodes", episodes),
                             ("scenario_tree_summaries", summaries),
                             ("scenario_representative_paths", representatives),
                             ("current_scenario_intelligence", current)):
            _insert(con, table, frame)
        details = {"synthetic_paths": False, "representatives": "actual_historical_medoids",
                   "historical_frequency_is_probability": False, "future_leakage": False,
                   "production_changes": 0, "probability_published": False}
        con.execute("UPDATE scenario_research_runs SET finished_at=current_timestamp,status='completed',"
                    "match_rows=?,prehistory_rows=?,episode_rows=?,summary_rows=?,details_json=? "
                    "WHERE run_id=?", [len(matches), len(prehistory), len(episodes), len(summaries),
                                        json.dumps(details), run_id])
        return {"run_id": run_id, "status": "completed", "matches": len(matches),
                "prehistory": len(prehistory), "episodes": len(episodes),
                "summaries": len(summaries), "current_rows": len(current), "cached": False}
    except Exception as exc:
        con.execute("UPDATE scenario_research_runs SET finished_at=current_timestamp,status='failed',"
                    "details_json=? WHERE run_id=?", [json.dumps({"error": str(exc)}), run_id])
        raise


def scenario_status(con: Any) -> dict[str, Any]:
    ensure_schema(con)
    row = con.execute("SELECT run_id,status,cutoff,match_rows,prehistory_rows,episode_rows,"
                      "summary_rows,details_json FROM scenario_research_runs "
                      "ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"latest": None}
    return dict(zip(("run_id", "status", "cutoff", "matches", "prehistory", "episodes",
                    "summaries", "details"), row, strict=True))
