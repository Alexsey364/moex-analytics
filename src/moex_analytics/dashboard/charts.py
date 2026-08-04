"""Plotly chart factories; no financial calculations."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def price_chart(frame: pd.DataFrame, candles: bool = False, log_scale: bool = False):
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22])
    if candles and {"open", "high", "low", "close"} <= set(frame.columns):
        figure.add_trace(
            go.Candlestick(
                x=frame.trade_date,
                open=frame.open,
                high=frame.high,
                low=frame.low,
                close=frame.close,
                name="Цена",
            ),
            row=1,
            col=1,
        )
    else:
        figure.add_trace(go.Scatter(x=frame.trade_date, y=frame.close, name="Закрытие"), row=1, col=1)
    if "volume" in frame:
        figure.add_trace(go.Bar(x=frame.trade_date, y=frame.volume, name="Объём"), row=2, col=1)
    figure.update_yaxes(type="log" if log_scale else "linear", row=1, col=1)
    figure.update_layout(hovermode="x unified", xaxis_rangeslider_visible=False, height=600)
    return figure


def return_chart(frame: pd.DataFrame):
    figure = go.Figure()
    if not frame.empty:
        figure.add_trace(
            go.Scatter(x=frame.trade_date, y=frame.total_return_index, name="Индекс полной доходности")
        )
    figure.update_layout(hovermode="x unified", height=360)
    return figure
