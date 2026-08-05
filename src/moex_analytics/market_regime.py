"""Transparent, configuration-driven IMOEX regime classification."""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

from .config import load_settings


def classify(values: dict, thresholds: dict) -> tuple[str, list[str]]:
    dd, vol = values.get("current_drawdown"), values.get("volatility_20")
    r20, r60 = values.get("return_20"), values.get("return_60")
    p50 = values.get("price_to_sma_50")
    p200, slope = values.get("price_to_sma_200"), values.get("sma_200_slope")
    if None in (dd, vol, r20, r60, p50, p200, slope):
        return "недостаточно данных", ["недостаточная история для правил"]
    if dd <= thresholds["stress_drawdown"] or vol >= thresholds["stress_volatility_20"]:
        return "стрессовый режим", ["глубокая просадка или высокая волатильность"]
    if dd <= thresholds["stress_drawdown"] / 2 and r20 >= thresholds["recovery_return_20"]:
        return "восстановление после стресса", ["сильный рост после заметной просадки"]
    if p50 > 0 and p200 > 0 and slope > 0 and r60 >= thresholds["strong_trend_return_60"]:
        return "устойчивый восходящий тренд", ["выше SMA50/200", "SMA200 растёт"]
    if p200 > 0 and r60 >= thresholds["moderate_trend_return_60"]:
        return "умеренный восходящий тренд", ["выше SMA200", "положительная доходность"]
    if p50 < 0 and p200 < 0 and slope < 0 and r60 <= -thresholds["strong_trend_return_60"]:
        return "устойчивый нисходящий тренд", ["ниже SMA50/200", "SMA200 снижается"]
    if p200 < 0 and r60 <= -thresholds["moderate_trend_return_60"]:
        return "умеренный нисходящий тренд", ["ниже SMA200", "отрицательная доходность"]
    return "боковой рынок", ["нет достаточного подтверждения направленного режима"]


def calculate_all(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_settings()["analytics"]
    version = cfg["calculation_version"]
    con.execute("DELETE FROM market_regimes WHERE calculation_version=?", [version])
    rows = con.execute(
        """SELECT trade_date,features_json FROM daily_features
        WHERE canonical_secid='IMOEX' AND calculation_version=? ORDER BY 1""",
        [version],
    ).fetchall()
    now = datetime.now()
    keys = (
        "price_to_sma_50",
        "price_to_sma_200",
        "sma_200_slope",
        "return_20",
        "return_60",
        "volatility_20",
        "current_drawdown",
    )
    for trade_date, raw in rows:
        values = json.loads(raw)
        regime, reasons = classify(values, cfg["regime"])
        used = {key: values.get(key) for key in keys}
        con.execute(
            "INSERT INTO market_regimes VALUES (?,?,?,?,?,?,?,?)",
            [
                trade_date,
                regime,
                json.dumps(reasons, ensure_ascii=False),
                json.dumps(used),
                version,
                now,
                cfg["source"],
                cfg["minimum_history"],
            ],
        )
    return len(rows)
