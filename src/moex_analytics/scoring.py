"""Transparent block scoring and historical status validation."""

from __future__ import annotations

import json
from datetime import date, datetime

import duckdb
import numpy as np

from .analogues import sample_quality
from .config import load_settings


def _bounded(value, scale=1.0):
    return 0.0 if value is None else float(np.clip(value / scale, -1, 1))


def score_blocks(features: dict, regime: str, analogue_frequency: float | None = None) -> dict:
    regime_scores = {
        "устойчивый восходящий тренд": 1,
        "умеренный восходящий тренд": 0.5,
        "боковой рынок": 0,
        "умеренный нисходящий тренд": -0.5,
        "устойчивый нисходящий тренд": -1,
        "стрессовый режим": -1,
        "восстановление после стресса": 0.25,
    }
    return {
        "trend": np.mean(
            [
                _bounded(features.get("price_to_sma_50"), 0.1),
                _bounded(features.get("price_to_sma_200"), 0.2),
            ]
        ),
        "momentum": np.mean(
            [
                _bounded(features.get("return_20"), 0.1),
                _bounded(features.get("return_60"), 0.2),
            ]
        ),
        "relative_strength": _bounded(features.get("relative_strength_60"), 0.15),
        "risk": -np.mean(
            [
                _bounded(features.get("volatility_60"), 0.5),
                _bounded(features.get("current_drawdown"), -0.3),
            ]
        ),
        "liquidity": _bounded((features.get("turnover_to_mean_20") or 1) - 1, 1),
        "market_regime": regime_scores.get(regime, 0),
        "historical_analogues": (
            0 if analogue_frequency is None else _bounded(analogue_frequency - 0.5, 0.25)
        ),
    }


def final_status(total: float, sample_size: int, thresholds: dict) -> str:
    if sample_size < 10:
        return "недостаточно данных"
    if total >= thresholds["statistically_favorable"]:
        return "статистически благоприятные условия"
    if total >= thresholds["moderately_favorable"]:
        return "умеренно благоприятные условия"
    if total <= thresholds["statistically_unfavorable"]:
        return "статистически неблагоприятные условия"
    if total <= thresholds["moderately_unfavorable"]:
        return "умеренно неблагоприятные условия"
    return "нейтральные условия"


def validation_period(trade_date: date, validation: dict) -> str:
    if trade_date <= date.fromisoformat(str(validation["development_end"])):
        return "development"
    if trade_date <= date.fromisoformat(str(validation["validation_end"])):
        return "validation"
    return "out-of-sample"


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["analytics"]
    version, scoring = cfg["calculation_version"], cfg["scoring"]
    con.execute("DELETE FROM instrument_scores WHERE calculation_version=?", [version])
    regime_map = dict(
        con.execute(
            "SELECT trade_date,regime FROM market_regimes WHERE calculation_version=?", [version]
        ).fetchall()
    )
    now, total = datetime.now(), 0
    secids = [row[0] for row in con.execute("SELECT DISTINCT canonical_secid FROM daily_features").fetchall()]
    for secid in secids:
        analogue_dates = [
            row[0]
            for row in con.execute(
                """SELECT analogue_date FROM historical_analogue_results
            WHERE canonical_secid=? AND calculation_version=?""",
                [secid, version],
            ).fetchall()
        ]
        outcomes = []
        if analogue_dates:
            outcomes = con.execute(
                """SELECT price_return FROM forward_returns WHERE canonical_secid=?
                AND horizon=20 AND condition_date IN (SELECT unnest(?))""",
                [secid, analogue_dates],
            ).fetchall()
        known = [row[0] for row in outcomes if row[0] is not None]
        frequency = sum(value > 0 for value in known) / len(known) if known else None
        rows = con.execute(
            """SELECT trade_date,features_json FROM daily_features
            WHERE canonical_secid=? AND calculation_version=? ORDER BY 1""",
            [secid, version],
        ).fetchall()
        latest_trade_date = rows[-1][0] if rows else None
        for trade_date, payload in rows:
            features = json.loads(payload)
            analogue_frequency = frequency if trade_date == latest_trade_date else None
            blocks = score_blocks(
                features,
                regime_map.get(trade_date, "недостаточно данных"),
                analogue_frequency,
            )
            weighted = sum(float(blocks[key]) * scoring["weights"][key] for key in blocks)
            status = final_status(weighted, len(known), scoring["thresholds"])
            positive = [key for key, value in blocks.items() if value > 0.15]
            negative = [key for key, value in blocks.items() if value < -0.15]
            con.execute(
                "INSERT INTO instrument_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    trade_date,
                    secid,
                    weighted,
                    status,
                    json.dumps(blocks),
                    json.dumps(positive),
                    json.dumps(negative),
                    sample_quality(len(known)),
                    version,
                    now,
                    cfg["source"],
                    cfg["minimum_history"],
                ],
            )
            total += 1
    return total
