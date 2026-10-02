"""
app.py -- Streamlit dashboard for the moving-average bounce scanner.

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
from plotly.subplots import make_subplots

from screener import HISTORY_PERIOD, SIGNALS, compute_rsi, moving_average, scan_watchlist

WATCHLIST_PATH = Path(__file__).parent / "data" / "watchlist.json"

st.set_page_config(
    page_title="Moving Average Bounce Scanner",
    layout="wide",
)


@st.cache_data(ttl=3600)
def load_watchlist_defaults() -> dict:
    with open(WATCHLIST_PATH, "r") as f:
        return json.load(f)


@st.cache_data(ttl=900)
def get_chart_history(ticker: str, rsi_period: int) -> pd.DataFrame:
    """Daily OHLC history with every moving average and RSI computed over the full series."""
    hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD, auto_adjust=True)
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.dropna(subset=["Close"])
    for key, (kind, window, _label) in SIGNALS.items():
        hist[key] = moving_average(hist["Close"], kind, window)
    hist["RSI"] = compute_rsi(hist["Close"], rsi_period)
    return hist


def run_scan(
    tickers: list[str],
    proximity_pct: float,
    earnings_lookback_days: int,
    bounce_lookback_days: int,
    rsi_period: int,
    rsi_threshold: float,
):
    with st.spinner(f"Scanning {len(tickers)} ticker(s)..."):
        results = scan_watchlist(
            tickers,
            proximity_pct=proximity_pct,
            earnings_lookback_days=earnings_lookback_days,
            bounce_lookback_days=bounce_lookback_days,
            rsi_period=rsi_period,
            rsi_threshold=rsi_threshold,
        )
    st.session_state["scan_results"] = results


def flatten_results(results: list[dict]) -> pd.DataFrame:
    """One row per ticker for the results table."""
    rows = []
    for r in results:
        row = {
            "Ticker": r["ticker"],
            "Price": r.get("current_close"),
            "RSI": r.get("rsi"),
            "Low RSI (window)": r.get("rsi_min_window"),
        }
        for key, (_kind, _window, label) in SIGNALS.items():
            sig = r.get("signals", {}).get(key, {})
            row[f"vs {label}"] = sig.get("dist_pct")
            row[f"{label} Bounce?"] = sig.get("is_bounce", False)
        row["Signals"] = ", ".join(SIGNALS[k][2] for k in r.get("signals_triggered", [])) or ""
        row["Last Earnings"] = r.get("last_earnings_date")
        row["Next Earnings"] = r.get("next_earnings_date")
        row["Error"] = r.get("error")
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    defaults = load_watchlist_defaults()

    st.title("Moving Average Bounce Scanner")
    st.caption(
        "Screens mega-cap tickers for a pullback to the 50 or 200-day EMA/SMA, with RSI "
        "dipping into oversold territory, followed by an upward reversal. The 200 EMA "
        "signal additionally requires a recent earnings report."
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
            options=sorted(set(default_tickers) | {"AAPL", "MSFT", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO", "NFLX"}),
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

        rsi_threshold = st.number_input(
            "RSI oversold threshold",
            min_value=5.0,
            max_value=50.0,
            value=float(defaults.get("rsi_oversold_threshold", 30)),
            step=1.0,
        )
        rsi_period = int(defaults.get("rsi_period", 14))

        run_clicked = st.button("Run Scan", type="primary", width="stretch")

    if run_clicked:
        if not all_tickers:
            st.sidebar.error("Add at least one ticker before scanning.")
        else:
            run_scan(all_tickers, proximity_pct, earnings_lookback_days, bounce_lookback_days, rsi_period, rsi_threshold)

    results = st.session_state.get("scan_results")

    if not results:
        st.info("Set your tickers and thresholds in the sidebar, then click **Run Scan**.")
        return

    df = flatten_results(results)
    match_mask = df["Signals"] != ""

    col1, col2 = st.columns(2)
    col1.metric("Total Scanned", len(df))
    col2.metric("Tickers With a Signal", int(match_mask.sum()))

    st.subheader("Scan Results")
    st.caption(
        "\"Bounce?\" columns show the price pattern alone. \"Signals\" lists the ones that "
        "also passed the RSI filter (and, for the 200 EMA, the recent-earnings filter) -- "
        "those are what trigger Discord alerts."
    )

    def highlight_matches(row):
        return ["background-color: #1e5631; color: white" if row["Signals"] else "" for _ in row]

    fmt = {"Price": "${:.2f}", "RSI": "{:.1f}", "Low RSI (window)": "{:.1f}"}
    fmt.update({f"vs {label}": "{:+.2f}%" for (_k, _w, label) in SIGNALS.values()})
    styled = df.style.apply(highlight_matches, axis=1).format(fmt, na_rep="N/A")
    st.dataframe(styled, width="stretch", hide_index=True)

    st.subheader("Detail View")
    tickers_with_data = df[df["Error"].isna()]["Ticker"].tolist()
    if not tickers_with_data:
        st.warning("No tickers returned usable price data to chart.")
        return

    selected = st.selectbox("Select a ticker to chart", options=tickers_with_data)
    if not selected:
        return

    hist = get_chart_history(selected, rsi_period)
    if hist.empty:
        st.warning(f"No chart data available for {selected}.")
        return

    six_months_ago = hist.index.max() - pd.Timedelta(days=182)
    chart_df = hist[hist.index >= six_months_ago]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
    fig.add_trace(
        go.Candlestick(
            x=chart_df.index,
            open=chart_df["Open"],
            high=chart_df["High"],
            low=chart_df["Low"],
            close=chart_df["Close"],
            name=selected,
        ),
        row=1,
        col=1,
    )
    line_styles = {
        "ema200": dict(color="orange", width=2),
        "sma200": dict(color="orange", width=2, dash="dot"),
        "ema50": dict(color="deepskyblue", width=1.5),
        "sma50": dict(color="deepskyblue", width=1.5, dash="dot"),
    }
    for key, (_kind, _window, label) in SIGNALS.items():
        fig.add_trace(
            go.Scatter(x=chart_df.index, y=chart_df[key], mode="lines", name=label, line=line_styles[key]),
            row=1,
            col=1,
        )
    fig.add_trace(
        go.Scatter(x=chart_df.index, y=chart_df["RSI"], mode="lines", name="RSI", line=dict(color="violet", width=1.5)),
        row=2,
        col=1,
    )
    fig.add_hline(y=rsi_threshold, line=dict(color="red", dash="dash", width=1), row=2, col=1)
    fig.update_yaxes(title_text="Price ($)", row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_layout(
        title=f"{selected} -- Last 6 Months with 50/200 EMA & SMA",
        xaxis_rangeslider_visible=False,
        height=700,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")


if __name__ == "__main__":
    main()
