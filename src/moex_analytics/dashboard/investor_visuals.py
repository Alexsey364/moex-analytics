"""Pure Plotly builders; they read no data and never recalculate predictive models."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from moex_analytics.dashboard.visual_semantics import color_for, forecast_marker, token_for

HORIZONS = (1, 5, 20, 60, 120, 250)


def horizon_heatmap(frame: pd.DataFrame) -> go.Figure:
    stocks = list(dict.fromkeys(frame.get("secid", pd.Series(dtype=str)).tolist()))
    lookup = {(r.secid, int(r.horizon)): r for r in frame.itertuples()}
    z, text, hover = [], [], []
    scale = {"negative": -1, "mixed": 0, "insufficient": 0.5, "positive": 1, "caution": -0.5, "neutral": 0.25}
    for stock in stocks:
        zr, tr, hr = [], [], []
        for horizon in HORIZONS:
            row = lookup.get((stock, horizon))
            token = token_for(getattr(row, "status", None))
            zr.append(scale[token.key])
            tr.append(token.symbol)
            hr.append(
                f"{stock} · {horizon} сессий<br>{token.label}<br>"
                f"Confidence: {getattr(row, 'confidence', '—')}<br>"
                f"Model: {getattr(row, 'model', '—')}<br>Sample: {getattr(row, 'sample', '—')}"
            )
        z.append(zr)
        text.append(tr)
        hover.append(hr)
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[f"{h}d" for h in HORIZONS],
            y=stocks,
            text=text,
            texttemplate="%{text}",
            customdata=hover,
            hovertemplate="%{customdata}<extra></extra>",
            zmin=-1,
            zmax=1,
            colorscale=[
                [0, "#cf222e"],
                [0.25, "#bc4c00"],
                [0.5, "#d4a72c"],
                [0.75, "#8c959f"],
                [1, "#16803c"],
            ],
            colorbar=dict(title="направление", tickvals=[-1, 0, 0.5, 1], ticktext=["↓", "→", "?", "↑"]),
        )
    )
    fig.update_layout(height=max(330, 38 * len(stocks)), margin=dict(l=10, r=10, t=20, b=10))
    return fig


def price_figure(
    prices: pd.DataFrame, forecasts: pd.DataFrame | None = None, averages: tuple[int, ...] = ()
) -> go.Figure:
    fig = go.Figure()
    if prices.empty:
        return fig
    data = prices.sort_values("trade_date").copy()
    fig.add_trace(
        go.Scatter(x=data.trade_date, y=data.close, name="Цена", line=dict(color="#0969da", width=2))
    )
    for window in averages:
        if len(data) >= window:
            fig.add_trace(
                go.Scatter(
                    x=data.trade_date,
                    y=data.close.rolling(window).mean(),
                    name=f"SMA{window}",
                    line=dict(width=1),
                )
            )
    if forecasts is not None and not forecasts.empty:
        for row in forecasts.itertuples():
            token = forecast_marker(
                getattr(row, "outcome_status", None),
                getattr(row, "direction_correct", None),
                getattr(row, "neutral_hit", None),
            )
            fig.add_trace(
                go.Scatter(
                    x=[row.cutoff],
                    y=[row.current_price],
                    mode="markers",
                    name=f"Forecast {getattr(row, 'horizon_sessions', '')}d",
                    marker=dict(
                        size=10,
                        color=color_for(token.key),
                        symbol="circle-open" if token.key == "insufficient" else "circle",
                    ),
                    customdata=[
                        [
                            getattr(row, "qualitative_direction", "—"),
                            getattr(row, "model_version", "—"),
                            getattr(row, "confidence", "—"),
                            getattr(row, "actual_return", None),
                        ]
                    ],
                    hovertemplate=(
                        "%{x}<br>Цена: %{y:.2f}<br>Вывод: %{customdata[0]}"
                        "<br>Model: %{customdata[1]}<br>Confidence: %{customdata[2]}"
                        "<br>Факт: %{customdata[3]}<extra></extra>"
                    ),
                )
            )
    fig.update_layout(
        hovermode="x unified", height=460, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h")
    )
    return fig


def terminal_range_figure(forecast: pd.Series, actual: pd.DataFrame) -> go.Figure:
    """Terminal intervals plus an actual path only; no synthetic forecast trajectory."""
    fig = go.Figure()
    x = int(forecast.get("horizon_sessions", 0))
    start = float(forecast.get("current_price", 0))
    for band, opacity in ((90, 0.12), (80, 0.2), (50, 0.32)):
        low, high = forecast.get(f"range_{band}_low"), forecast.get(f"range_{band}_high")
        if pd.notna(low) and pd.notna(high):
            fig.add_trace(
                go.Scatter(
                    x=[x, x],
                    y=[start * (1 + low), start * (1 + high)],
                    mode="lines",
                    name=f"{band}% terminal interval",
                    line=dict(width=16, color=f"rgba(9,105,218,{opacity})"),
                )
            )
    median = forecast.get("median_return")
    if pd.notna(median):
        fig.add_trace(
            go.Scatter(
                x=[x],
                y=[start * (1 + median)],
                mode="markers",
                name="Expected median",
                marker=dict(size=11, symbol="diamond"),
            )
        )
    if not actual.empty:
        path = actual.sort_values("trade_date").reset_index(drop=True)
        fig.add_trace(
            go.Scatter(
                x=np.arange(1, len(path) + 1),
                y=path.close,
                name="Фактическая траектория",
                line=dict(color="#24292f"),
            )
        )
    fig.add_trace(go.Scatter(x=[0], y=[start], mode="markers", name="Точка старта"))
    fig.update_layout(xaxis_title="Торговые сессии", yaxis_title="Цена", height=390)
    return fig


def signed_bar(frame: pd.DataFrame, label: str, value: str, title: str) -> go.Figure:
    data = frame.sort_values(value)
    colors = [color_for("positive" if x >= 0 else "negative") for x in data[value]]
    fig = go.Figure(
        go.Bar(
            x=data[value],
            y=data[label],
            orientation="h",
            marker_color=colors,
            text=data[value].map(lambda x: f"{x:+.1%}"),
            textposition="auto",
        )
    )
    fig.update_layout(title=title, height=max(300, len(data) * 34), margin=dict(l=10, r=10, t=45, b=10))
    return fig


def risk_weight_figure(frame: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(y=frame.secid, x=frame.equity_weight, name="Вес", orientation="h"))
    fig.add_trace(go.Bar(y=frame.secid, x=frame.risk_contribution, name="Вклад в риск", orientation="h"))
    fig.update_layout(
        barmode="group",
        xaxis_tickformat=".0%",
        height=max(360, len(frame) * 42),
        legend=dict(orientation="h"),
    )
    return fig


def breadth_figure(frame: pd.DataFrame) -> go.Figure:
    data = frame.sort_values("trade_date").copy()
    base = data.tradable_count.replace(0, np.nan)
    fig = go.Figure()
    for column, name in (
        ("above_sma200", "Выше SMA200"),
        ("above_sma50", "Выше SMA50"),
        ("advancing", "Растут"),
    ):
        fig.add_trace(go.Scatter(x=data.trade_date, y=data[column] / base, name=name))
    fig.update_layout(yaxis_tickformat=".0%", hovermode="x unified", height=400, legend=dict(orientation="h"))
    return fig


def live_progress(correct: int, wrong: int, neutral: int, pending: int) -> go.Figure | None:
    matured = correct + wrong + neutral
    if matured == 0:
        return None
    fig = go.Figure()
    for name, value, color in (
        ("Верно", correct, color_for("positive")),
        ("Ошибки", wrong, color_for("negative")),
        ("Нейтрально", neutral, color_for("mixed")),
        ("Ожидает", pending, color_for("insufficient")),
    ):
        fig.add_trace(
            go.Bar(
                x=[value], y=["Live forecasts"], orientation="h", name=name, marker_color=color, text=[value]
            )
        )
    fig.update_layout(barmode="stack", height=180, legend=dict(orientation="h"))
    return fig


def scenario_figure(frame: pd.DataFrame) -> go.Figure:
    fig = signed_bar(
        frame, "secid", "mechanical_sensitivity", "Historical sensitivity / scenario — не прогноз"
    )
    fig.update_layout(xaxis_title="Механическая чувствительность")
    return fig
