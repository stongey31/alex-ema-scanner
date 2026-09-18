"""
app.py -- Streamlit dashboard for the Post-Earnings 200 EMA Scanner.

This is for interactive browsing only. Any ticker/threshold changes made in
the sidebar here are session-only: they are NOT saved anywhere and have NO
effect on the automated daily scan (that always reads data/watchlist.json,
see runner.py). To permanently change what gets scanned/alerted on
automatically, edit data/watchlist.json directly (see README.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from screener import scan_watchlist

WATCHLIST_PATH = Path(__file__).parent / "data" / "watchlist.json"

st.set_page_config(
    page_title="Post-Earnings 200 EMA Scanner",
    layout="wide",
)


@st.cache_data(ttl=3600)
def load_watchlist_defaults() -> dict:
    with open(WATCHLIST_PATH, "r") as f:
        return json.load(f)


@st.cache_data(ttl=900)
def get_chart_history(ticker: str) -> pd.DataFrame:
    """1y of OHLC history with the 200 EMA computed over the full series."""
    hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.dropna(subset=["Close"])
    hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()
    return hist


def run_scan(tickers: list[str], proximity_pct: float, earnings_lookback_days: int, bounce_lookback_days: int):
    with st.spinner(f"Scanning {len(tickers)} ticker(s)..."):
        results = scan_watchlist(
            tickers,
            proximity_pct=proximity_pct,
            earnings_lookback_days=earnings_lookback_days,
            bounce_lookback_days=bounce_lookback_days,
        )
    st.session_state["scan_results"] = results


def main():
    defaults = load_watchlist_defaults()

    st.title("Post-Earnings 200 EMA Scanner")
    st.caption(
        "Screens mega-cap tickers for a pullback to the 200-day EMA followed by an "
        "upward reversal, shortly after an earnings report."
    )

    with st.sidebar:
        st.header("Scan Settings")
        st.caption(
            "⚠️ Changes here are for browsing only, for this session. They are "
            "**not saved** and do **not** affect the automated daily Discord "
            "alerts. To permanently change the monitored tickers or thresholds, "
            "hand-edit `data/watchlist.json` on GitHub (see README)."
        )

        default_tickers = defaults.get("tickers", [])
        selected_tickers = st.multiselect(
            "Tickers (session only)",
            options=sorted(set(default_tickers) | {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO", "NFLX"}),
            default=default_tickers,
        )
        extra_tickers_raw = st.text_input("Add other tickers (comma-separated)", value="")
        extra_tickers = [t.strip().upper() for t in extra_tickers_raw.split(",") if t.strip()]
        all_tickers = list(dict.fromkeys(selected_tickers + extra_tickers))

        proximity_pct = st.number_input(
            "Proximity threshold (%)",
            min_value=0.1,
            max_value=10.0,
            value=float(defaults.get("proximity_threshold_pct", 2.0)),
            step=0.1,
        )
        earnings_lookback_days = st.number_input(
            "Earnings lookback (days)",
            min_value=1,
            max_value=60,
            value=int(defaults.get("earnings_lookback_days", 7)),
            step=1,
        )
        bounce_lookback_days = st.number_input(
            "Bounce lookback (trading sessions)",
            min_value=2,
            max_value=60,
            value=int(defaults.get("bounce_lookback_days", 10)),
            step=1,
        )

        run_clicked = st.button("Run Scan", type="primary", width="stretch")

    if run_clicked:
        if not all_tickers:
            st.sidebar.error("Add at least one ticker before scanning.")
        else:
            run_scan(all_tickers, proximity_pct, earnings_lookback_days, bounce_lookback_days)

    results = st.session_state.get("scan_results")

    if not results:
        st.info("Set your tickers and thresholds in the sidebar, then click **Run Scan**.")
        return

    df = pd.DataFrame(results)
    matches = df[df["is_bounce"] & df["earnings_recent"]]

    col1, col2 = st.columns(2)
    col1.metric("Total Scanned", len(df))
    col2.metric("Matching Setups", len(matches))

    st.subheader("Scan Results")

    display_df = df.copy()
    display_df["Match"] = display_df["is_bounce"] & display_df["earnings_recent"]
    display_df = display_df.rename(
        columns={
            "ticker": "Ticker",
            "current_close": "Price",
            "ema200": "200 EMA",
            "dist_pct": "% Dist",
            "is_bounce": "Bounce?",
            "earnings_recent": "Recent Earnings?",
            "last_earnings_date": "Last Earnings",
            "error": "Error",
        }
    )
    display_cols = ["Ticker", "Price", "200 EMA", "% Dist", "Bounce?", "Recent Earnings?", "Last Earnings", "Match", "Error"]

    def highlight_matches(row):
        return ["background-color: #1e5631; color: white" if row["Match"] else "" for _ in row]

    styled = display_df[display_cols].style.apply(highlight_matches, axis=1).format(
        {"Price": "${:.2f}", "200 EMA": "${:.2f}", "% Dist": "{:+.2f}%"}, na_rep="N/A"
    )
    st.dataframe(styled, width="stretch", hide_index=True)

    st.subheader("Detail View")
    tickers_with_data = df[df["error"].isna()]["ticker"].tolist()
    if not tickers_with_data:
        st.warning("No tickers returned usable price data to chart.")
        return

    selected = st.selectbox("Select a ticker to chart", options=tickers_with_data)
    if not selected:
        return

    hist = get_chart_history(selected)
    if hist.empty:
        st.warning(f"No chart data available for {selected}.")
        return

    six_months_ago = hist.index.max() - pd.Timedelta(days=182)
    chart_df = hist[hist.index >= six_months_ago]

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=chart_df.index,
            open=chart_df["Open"],
            high=chart_df["High"],
            low=chart_df["Low"],
            close=chart_df["Close"],
            name=selected,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=chart_df.index,
            y=chart_df["EMA200"],
            mode="lines",
            name="200 EMA",
            line=dict(color="orange", width=2),
        )
    )
    fig.update_layout(
        title=f"{selected} -- Last 6 Months with 200-Day EMA",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        xaxis_rangeslider_visible=False,
        height=550,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")


if __name__ == "__main__":
    main()
