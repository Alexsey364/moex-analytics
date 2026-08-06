"""Daily event reaction calculations."""

from statistics import pstdev

from .point_in_time import anchor_session
from .quality import confounding_status

WINDOWS = {"session": 0, "next_session": 1, "1d": 1, "3d": 3, "5d": 5, "20d": 20, "60d": 60}


def reaction(
    prices: list[tuple], market: dict, event_time, same_day_events=1, dividend_day=False
) -> list[dict]:
    dates = [x[0] for x in prices]
    anchor = next((i for i, d in enumerate(dates) if d >= event_time.date()), None)
    if anchor is None:
        return []
    out = []
    for name, horizon in WINDOWS.items():
        end = anchor + horizon
        if end >= len(prices):
            continue
        path = prices[anchor : end + 1]
        raw = path[-1][1] / path[0][1] - 1 if horizon else 0.0
        imoex = (
            (market.get(path[-1][0], market.get(path[0][0])) / market[path[0][0]] - 1)
            if path[0][0] in market and path[-1][0] in market
            else None
        )
        abnormal = raw - imoex if imoex is not None else None
        closes = [x[1] for x in path]
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        peak = max(closes)
        trough = min(closes)
        gain = peak / closes[0] - 1
        draw = trough / closes[0] - 1
        status, factors = confounding_status(same_day_events, abs(imoex or 0), dividend_day)
        out.append(
            {
                "window": name,
                "anchor": path[0][0],
                "exit": path[-1][0],
                "raw": raw,
                "imoex": imoex,
                "abnormal": abnormal,
                "volume_change": None,
                "volatility_change": pstdev(returns) if len(returns) > 1 else None,
                "max_gain": gain,
                "max_drawdown": draw,
                "sessions_to_max": closes.index(peak),
                "session": anchor_session(event_time.hour),
                "confounding": status,
                "confounders": factors,
            }
        )
    return out
