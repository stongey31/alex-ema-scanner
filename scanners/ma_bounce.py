"""
screener.py -- Moving-average bounce scanner analysis engine.

Four separate signals are checked per ticker, one per moving average:

    ema200  -- 200-day EMA   (the original signal)
    sma200  -- 200-day SMA
    ema50   -- 50-day EMA
    sma50   -- 50-day SMA

Bounce definition (identical for every moving average -- read this before
touching the logic):

  1. Pull 2 years of daily history for a ticker and compute each moving
     average over the *entire* series:
       EMA: hist['Close'].ewm(span=N, adjust=False).mean()
       SMA: hist['Close'].rolling(window=N).mean()
     Two years (not one) so the 200-day lines have enough history to be
     reliable: a 200 SMA needs 200 rows before it has any value at all, and
     a 200 EMA needs a long run-up to settle.
  2. Look at the last `bounce_lookback_days` trading sessions (default 10,
     including today). For each of those days, compare that day's close to
     that day's own moving-average value. If any day's close was within
     `proximity_pct` (default 2.0%) of that day's average, the ticker
     "touched" the average. Of all touch days in the window, the one with the
     LOWEST close is recorded as *the* touch day (the deepest point of the
     pullback).
  3. The bounce is only confirmed if, in addition to having touched:
       (a) today's close is above today's moving-average value, AND
       (b) today's close is higher than the close on the recorded touch day.
     This requires an actual upward reversal off the average, not just "price
     happens to be near the average right now" and not a continued slide
     through it. It intentionally does NOT fire on the mirror-image case
     (price falling back down through the average from above, i.e.
     resistance rejection) -- only "pullback to the average, then bounce
     upward."

RSI filter (applies to all four signals):

  RSI is the standard 14-day RSI using Wilder's smoothing. A signal only
  triggers if RSI dipped below `rsi_threshold` (default 30, i.e. oversold)
  on ANY day in the same `bounce_lookback_days` window -- that is, at some
  point during the pullback, not necessarily today. By the time a bounce is
  confirmed RSI has usually already climbed back off its low, so requiring
  RSI < 30 *today* would almost never fire. The intended pattern is
  "oversold during the pullback, then price reversed upward."

Earnings:

  `earnings_recent` is True if the most recent PAST earnings date (from
  yfinance's get_earnings_dates) falls within the last `earnings_lookback_days`
  days (default 7). It is a required condition for the ema200 signal ONLY
  (that's the original post-earnings setup). For the other three signals,
  earnings is informational: every result reports `last_earnings_date` and
  `next_earnings_date` so they can be shown in alerts and the dashboard.

  yfinance's earnings-date data is known to be inconsistent (sometimes
  tz-aware, sometimes not, sometimes empty/None, sometimes only future
  estimated dates) -- all of that is handled defensively below so a bad or
  missing earnings calendar for one ticker never crashes the scan.

Signal trigger summary:

    ema200: bounce + RSI < threshold during window + recent earnings
    sma200: bounce + RSI < threshold during window
    ema50:  bounce + RSI < threshold during window
    sma50:  bounce + RSI < threshold during window

Every per-ticker failure (network hiccup, delisted ticker, missing data) is
caught and logged; it never halts the rest of the scan.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from core.scanner_base import RunContext, ScanOutput, Scanner, Signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("screener")

DEFAULT_PROXIMITY_PCT = 2.0
DEFAULT_EARNINGS_LOOKBACK_DAYS = 7
DEFAULT_BOUNCE_LOOKBACK_DAYS = 10
DEFAULT_RSI_PERIOD = 14
DEFAULT_RSI_THRESHOLD = 30.0
HISTORY_PERIOD = "2y"
MIN_HISTORY_ROWS = 200

# key -> (kind, window, human label). Order here is the order signals are
# reported in alerts and the dashboard.
SIGNALS: dict[str, tuple[str, int, str]] = {
    "ema200": ("ema", 200, "200 EMA"),
    "sma200": ("sma", 200, "200 SMA"),
    "ema50": ("ema", 50, "50 EMA"),
    "sma50": ("sma", 50, "50 SMA"),
}

# Signals that additionally require a recent earnings report to trigger.
EARNINGS_GATED_SIGNALS = {"ema200"}


def signal_label(key: str) -> str:
    return SIGNALS[key][2]


def _empty_signal() -> dict:
    return {
        "value": None,
        "dist_pct": None,
        "touched": False,
        "touch_date": None,
        "touch_close": None,
        "is_bounce": False,
        "triggered": False,
    }


def _empty_result(ticker: str, error: str) -> dict:
    return {
        "ticker": ticker,
        "current_close": None,
        "rsi": None,
        "rsi_min_window": None,
        "rsi_oversold": False,
        "window_start": None,
        "earnings_recent": False,
        "last_earnings_date": None,
        "next_earnings_date": None,
        "signals": {key: _empty_signal() for key in SIGNALS},
        "signals_triggered": [],
        "error": error,
    }


def _naive_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Strip timezone info so date-math never raises tz-aware/naive comparison errors."""
    if getattr(idx, "tz", None) is not None:
        return idx.tz_convert("UTC").tz_localize(None)
    return idx


def compute_rsi(close: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> pd.Series:
    """Standard RSI with Wilder's smoothing -- matches TradingView/StockCharts.

    Seeded with the simple average gain/loss of the first `period` changes,
    then smoothed recursively: avg = (prev_avg * (period - 1) + current) / period.
    Values before there's enough data are NaN. A stretch with no losses gives
    RSI 100; no gains gives RSI 0; a completely flat stretch gives 50.
    """
    delta = close.diff().to_numpy()
    rsi = np.full(len(close), np.nan)
    if len(close) <= period:
        return pd.Series(rsi, index=close.index)

    gains = np.clip(delta, 0, None)
    losses = np.clip(-delta, 0, None)
    avg_gain = gains[1 : period + 1].mean()
    avg_loss = losses[1 : period + 1].mean()

    def _rsi(g: float, l: float) -> float:
        if g == 0 and l == 0:
            return 50.0
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    rsi[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i] = _rsi(avg_gain, avg_loss)
    return pd.Series(rsi, index=close.index)


def moving_average(close: pd.Series, kind: str, window: int) -> pd.Series:
    if kind == "ema":
        return close.ewm(span=window, adjust=False).mean()
    if kind == "sma":
        return close.rolling(window=window).mean()
    raise ValueError(f"unknown moving average kind: {kind}")


def detect_bounce(close: pd.Series, ma: pd.Series, window_n: int, proximity_pct: float) -> dict:
    """Apply the touch-then-bounce rule to one moving average. Never raises."""
    result = _empty_signal()

    today_close = float(close.iloc[-1])
    today_ma = ma.iloc[-1]
    if pd.isna(today_ma):
        return result
    today_ma = float(today_ma)

    result["value"] = round(today_ma, 2)
    result["dist_pct"] = round((today_close - today_ma) / today_ma * 100.0, 2)

    touch_date = None
    touch_close = None
    for date, close_val, ma_val in zip(close.index[-window_n:], close.values[-window_n:], ma.values[-window_n:]):
        if pd.isna(ma_val):
            continue
        day_dist_pct = abs(float(close_val) - float(ma_val)) / float(ma_val) * 100.0
        if day_dist_pct <= proximity_pct:
            if touch_close is None or float(close_val) < touch_close:
                touch_date = date
                touch_close = float(close_val)

    result["touched"] = touch_close is not None
    result["touch_date"] = touch_date.strftime("%Y-%m-%d") if touch_date is not None else None
    result["touch_close"] = round(touch_close, 2) if touch_close is not None else None
    result["is_bounce"] = bool(
        touch_close is not None and today_close > today_ma and today_close > touch_close
    )
    return result


def _check_earnings(
    ticker_obj: yf.Ticker, lookback_days: int
) -> tuple[bool, Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Return (earnings_recent, last_earnings_date, next_earnings_date). Never raises."""
    try:
        earnings_df = ticker_obj.get_earnings_dates(limit=12)
    except Exception as e:
        log.warning("get_earnings_dates failed: %s", e)
        return False, None, None

    if earnings_df is None or earnings_df.empty:
        return False, None, None

    try:
        idx = _naive_index(earnings_df.index)
        now = pd.Timestamp.utcnow().tz_localize(None)
        past = idx[idx <= now]
        future = idx[idx > now]
        next_earnings_date = future.min() if len(future) else None
        if len(past) == 0:
            return False, None, next_earnings_date
        last_earnings_date = past.max()
        days_since = (now - last_earnings_date).days
        earnings_recent = 0 <= days_since <= lookback_days
        return earnings_recent, last_earnings_date, next_earnings_date
    except Exception as e:
        log.warning("earnings date normalization failed for %s: %s", ticker_obj.ticker, e)
        return False, None, None


def evaluate_history(
    hist: pd.DataFrame,
    proximity_pct: float = DEFAULT_PROXIMITY_PCT,
    bounce_lookback_days: int = DEFAULT_BOUNCE_LOOKBACK_DAYS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    rsi_threshold: float = DEFAULT_RSI_THRESHOLD,
) -> dict:
    """Compute every price-based field from a daily OHLC history.

    Split out from analyze_ticker (which adds earnings and the ema200
    earnings gate) so the price logic can be tested on synthetic data
    without network access. Returns signals with `triggered` reflecting the
    bounce + RSI conditions only.
    """
    close = hist["Close"]
    window_n = min(bounce_lookback_days, len(close))

    rsi_series = compute_rsi(close, rsi_period)
    rsi_today = rsi_series.iloc[-1]
    rsi_window_min = rsi_series.iloc[-window_n:].min()
    rsi_oversold = bool(pd.notna(rsi_window_min) and rsi_window_min < rsi_threshold)

    signals = {}
    for key, (kind, window, _label) in SIGNALS.items():
        sig = detect_bounce(close, moving_average(close, kind, window), window_n, proximity_pct)
        sig["triggered"] = bool(sig["is_bounce"] and rsi_oversold)
        signals[key] = sig

    return {
        "current_close": round(float(close.iloc[-1]), 2),
        "rsi": round(float(rsi_today), 1) if pd.notna(rsi_today) else None,
        "rsi_min_window": round(float(rsi_window_min), 1) if pd.notna(rsi_window_min) else None,
        "rsi_oversold": rsi_oversold,
        "window_start": close.index[-window_n].strftime("%Y-%m-%d"),
        "signals": signals,
    }


def analyze_ticker(
    ticker: str,
    proximity_pct: float = DEFAULT_PROXIMITY_PCT,
    earnings_lookback_days: int = DEFAULT_EARNINGS_LOOKBACK_DAYS,
    bounce_lookback_days: int = DEFAULT_BOUNCE_LOOKBACK_DAYS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    rsi_threshold: float = DEFAULT_RSI_THRESHOLD,
) -> dict:
    """Analyze a single ticker. Always returns a result dict; never raises."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=HISTORY_PERIOD, auto_adjust=True)

        if hist is None or hist.empty:
            return _empty_result(ticker, "no price history returned")

        hist = hist.dropna(subset=["Close"])
        if len(hist) < MIN_HISTORY_ROWS:
            return _empty_result(ticker, f"insufficient history ({len(hist)} rows, need >= {MIN_HISTORY_ROWS})")

        result = evaluate_history(
            hist,
            proximity_pct=proximity_pct,
            bounce_lookback_days=bounce_lookback_days,
            rsi_period=rsi_period,
            rsi_threshold=rsi_threshold,
        )

        earnings_recent, last_earnings_date, next_earnings_date = _check_earnings(t, earnings_lookback_days)
        for key in EARNINGS_GATED_SIGNALS:
            result["signals"][key]["triggered"] = bool(result["signals"][key]["triggered"] and earnings_recent)

        result.update(
            {
                "ticker": ticker,
                "earnings_recent": earnings_recent,
                "last_earnings_date": last_earnings_date.strftime("%Y-%m-%d") if last_earnings_date is not None else None,
                "next_earnings_date": next_earnings_date.strftime("%Y-%m-%d") if next_earnings_date is not None else None,
                "signals_triggered": [k for k, s in result["signals"].items() if s["triggered"]],
                "error": None,
            }
        )
        return result

    except Exception as e:
        log.exception("Failed to analyze %s", ticker)
        return _empty_result(ticker, str(e))


def scan_watchlist(
    tickers: list[str],
    proximity_pct: float = DEFAULT_PROXIMITY_PCT,
    earnings_lookback_days: int = DEFAULT_EARNINGS_LOOKBACK_DAYS,
    bounce_lookback_days: int = DEFAULT_BOUNCE_LOOKBACK_DAYS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    rsi_threshold: float = DEFAULT_RSI_THRESHOLD,
) -> list[dict]:
    """Scan every ticker, catching failures per-ticker so one bad symbol never halts the scan."""
    results = []
    for ticker in tickers:
        try:
            result = analyze_ticker(
                ticker,
                proximity_pct=proximity_pct,
                earnings_lookback_days=earnings_lookback_days,
                bounce_lookback_days=bounce_lookback_days,
                rsi_period=rsi_period,
                rsi_threshold=rsi_threshold,
            )
        except Exception as e:
            log.exception("Unexpected top-level failure scanning %s", ticker)
            result = _empty_result(ticker, str(e))
        results.append(result)
    return results


COLOR_GREEN = 0x2ECC71


def _days_since(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except ValueError:
        return None


def _fmt_date(date_str: Optional[str], days: Optional[int], past: bool) -> str:
    if not date_str:
        return "N/A"
    if days is None:
        return date_str
    if past:
        return f"{date_str} ({days}d ago)"
    return f"{date_str} (in {-days}d)"


def _join_labels(labels: list[str]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def build_embed(setup_data: dict, signal_keys: list[str]) -> dict:
    ticker = setup_data.get("ticker", "?")
    price = setup_data.get("current_close")
    rsi = setup_data.get("rsi")
    rsi_min = setup_data.get("rsi_min_window")
    signals = setup_data.get("signals", {})
    last_earnings_date = setup_data.get("last_earnings_date")
    next_earnings_date = setup_data.get("next_earnings_date")

    labels = [signal_label(k) for k in signal_keys]
    tradingview_link = f"https://www.tradingview.com/symbols/{ticker}"

    fields = [
        {"name": "Current Price", "value": f"${price:,.2f}" if price is not None else "N/A", "inline": True},
        {"name": "RSI (14) Now", "value": f"{rsi:.1f}" if rsi is not None else "N/A", "inline": True},
        {
            "name": "Lowest RSI in Pullback",
            "value": f"{rsi_min:.1f}" if rsi_min is not None else "N/A",
            "inline": True,
        },
    ]
    for key in signal_keys:
        sig = signals.get(key, {})
        value = sig.get("value")
        dist = sig.get("dist_pct")
        touch_date = sig.get("touch_date")
        lines = [
            f"Line: ${value:,.2f}" if value is not None else "Line: N/A",
            f"Price vs line: {dist:+.2f}%" if dist is not None else "Price vs line: N/A",
            f"Touched: {touch_date}" if touch_date else "Touched: N/A",
        ]
        fields.append({"name": f"Bounce off {signal_label(key)}", "value": "\n".join(lines), "inline": True})

    fields += [
        {
            "name": "Last Earnings",
            "value": _fmt_date(last_earnings_date, _days_since(last_earnings_date), past=True),
            "inline": True,
        },
        {
            "name": "Next Earnings",
            "value": _fmt_date(next_earnings_date, _days_since(next_earnings_date), past=False),
            "inline": True,
        },
        {"name": "Chart", "value": f"[View on TradingView]({tradingview_link})", "inline": False},
    ]

    description = (
        f"Price pulled back to the {_join_labels(labels)} with RSI dipping into oversold territory, "
        "then reversed upward."
    )
    if "ema200" in signal_keys:
        description += " The 200 EMA bounce also came shortly after an earnings report."

    return {
        "embeds": [
            {
                "title": f"{ticker}: Bounce off {_join_labels(labels)}",
                "description": description,
                "color": COLOR_GREEN,
                "fields": fields,
                "url": tradingview_link,
            }
        ]
    }


# ---------------------------------------------------------------------------
# Plugin wrapper: everything above is the original (unchanged) bounce logic.
# ---------------------------------------------------------------------------


def _flatten_results(results: list[dict]) -> pd.DataFrame:
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


_chart_history_cached = None


def _get_chart_history(ticker: str, rsi_period: int) -> pd.DataFrame:
    """Daily OHLC history with every moving average and RSI computed over the full series (cached 15 min)."""
    global _chart_history_cached
    if _chart_history_cached is None:
        import streamlit as st

        @st.cache_data(ttl=900)
        def _load(ticker: str, rsi_period: int) -> pd.DataFrame:
            hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD, auto_adjust=True)
            if hist is None or hist.empty:
                return pd.DataFrame()
            hist = hist.dropna(subset=["Close"])
            for key, (kind, window, _label) in SIGNALS.items():
                hist[key] = moving_average(hist["Close"], kind, window)
            hist["RSI"] = compute_rsi(hist["Close"], rsi_period)
            return hist

        _chart_history_cached = _load
    return _chart_history_cached(ticker, rsi_period)


class MaBounceScanner(Scanner):
    name = "Moving Average Bounce"
    description = (
        "Screens tickers for a pullback to the 50 or 200-day EMA/SMA, with RSI dipping into "
        "oversold territory, followed by an upward reversal. The 200 EMA signal additionally "
        "requires a recent earnings report."
    )
    lane = "daily"
    default_config = {
        "watchlist": "mega_caps",
        "proximity_threshold_pct": 2.0,
        "earnings_lookback_days": 7,
        "bounce_lookback_days": 10,
        "rsi_period": 14,
        "rsi_oversold_threshold": 30,
    }
    setting_meta = {
        "proximity_threshold_pct": {"label": "Proximity threshold (%)", "min": 0.1, "max": 10.0, "step": 0.1},
        "earnings_lookback_days": {"label": "Earnings lookback (days)", "min": 1, "max": 60, "step": 1},
        "bounce_lookback_days": {"label": "Bounce lookback (trading sessions)", "min": 2, "max": 60, "step": 1},
        "rsi_period": {"label": "RSI period", "min": 2, "max": 50, "step": 1},
        "rsi_oversold_threshold": {"label": "RSI oversold threshold", "min": 5, "max": 50, "step": 1},
    }
    column_formats = {
        "Price": "${:.2f}",
        "RSI": "{:.1f}",
        "Low RSI (window)": "{:.1f}",
        **{f"vs {label}": "{:+.2f}%" for (_k, _w, label) in SIGNALS.values()},
    }

    def run(self, tickers, config, ctx: RunContext) -> ScanOutput:
        results = scan_watchlist(
            tickers,
            proximity_pct=config.get("proximity_threshold_pct", 2.0),
            earnings_lookback_days=config.get("earnings_lookback_days", 7),
            bounce_lookback_days=config.get("bounce_lookback_days", 10),
            rsi_period=config.get("rsi_period", 14),
            rsi_threshold=config.get("rsi_oversold_threshold", 30),
        )
        rows = [{**r, "flagged": bool(r.get("signals_triggered"))} for r in results]
        signals = [
            Signal(r["ticker"], f'{r["ticker"]}:{k}', r["signals"][k]["touch_date"], r.get("window_start"))
            for r in results
            for k in r["signals_triggered"]
        ]
        return ScanOutput("ok", "", rows, signals)

    def table(self, output: ScanOutput) -> pd.DataFrame:
        return _flatten_results(output.rows)

    def format_alert(self, ticker, signals, row) -> dict:
        return build_embed(row, [s.key.split(":", 1)[1] for s in signals])

    def render_detail(self, output: ScanOutput, config: dict) -> None:
        import streamlit as st

        from core.charts import daily_candles

        st.subheader("Detail View")
        tickers_with_data = [r["ticker"] for r in output.rows if not r.get("error")]
        if not tickers_with_data:
            st.warning("No tickers returned usable price data to chart.")
            return
        selected = st.selectbox("Select a ticker to chart", options=tickers_with_data, key=f"detail_{self.id}")
        if not selected:
            return
        rsi_period = int(config.get("rsi_period", 14))
        hist = _get_chart_history(selected, rsi_period)
        if hist.empty:
            st.warning(f"No chart data available for {selected}.")
            return
        six_months_ago = hist.index.max() - pd.Timedelta(days=182)
        chart_df = hist[hist.index >= six_months_ago]
        line_styles = {
            "ema200": dict(color="orange", width=2),
            "sma200": dict(color="orange", width=2, dash="dot"),
            "ema50": dict(color="deepskyblue", width=1.5),
            "sma50": dict(color="deepskyblue", width=1.5, dash="dot"),
        }
        overlays = [(key, label, line_styles[key]) for key, (_k, _w, label) in SIGNALS.items()]
        fig = daily_candles(
            chart_df,
            f"{selected} -- Last 6 Months with 50/200 EMA & SMA",
            overlays,
            lower=("RSI", "RSI", dict(color="violet", width=1.5), config.get("rsi_oversold_threshold", 30), [0, 100]),
            name=selected,
        )
        st.plotly_chart(fig, width="stretch")
