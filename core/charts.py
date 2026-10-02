"""Plotly chart builders shared by scanners."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def daily_candles(
    df: pd.DataFrame,
    title: str,
    overlays: list[tuple[str, str, dict]],
    lower: Optional[tuple[str, str, dict, float, list]] = None,
    name: str = "",
) -> go.Figure:
    """Candles + overlay lines (col, label, line_dict); optional lower panel
    (col, label, line_dict, hline_y, yrange)."""
    two_rows = lower is not None
    if two_rows:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
    else:
        fig = make_subplots(rows=1, cols=1)
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name=name,
        ),
        row=1,
        col=1,
    )
    for col, label, line in overlays:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=label, line=line), row=1, col=1)
    fig.update_yaxes(title_text="Price ($)", row=1, col=1)
    if two_rows:
        col, label, line, hline_y, yrange = lower
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=label, line=line), row=2, col=1)
        fig.add_hline(y=hline_y, line=dict(color="red", dash="dash", width=1), row=2, col=1)
        fig.update_yaxes(title_text=label, range=yrange, row=2, col=1)
    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        height=700,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig


def intraday_candles(df: pd.DataFrame, title: str, hlines: list[tuple[float, str]] = ()) -> go.Figure:
    fig = go.Figure(
        go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="5m")
    )
    for y, label in hlines:
        fig.add_hline(y=y, line=dict(color="orange", dash="dash", width=1), annotation_text=label)
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=500, hovermode="x unified")
    return fig
