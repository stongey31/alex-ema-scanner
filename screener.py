"""
screener.py -- Post-Earnings 200 EMA Scanner analysis engine.

Bounce definition (read this before touching the logic):

  1. Pull 1 year of daily history for a ticker and compute the 200-day EMA
     over the *entire* series: hist['Close'].ewm(span=200, adjust=False).mean().
  2. Look at the last `bounce_lookback_days` trading sessions (default 10,
     including today). For each of those days, compare that day's close to
     that day's own 200 EMA value. If any day's close was within
     `proximity_pct` (default 2.0%) of that day's EMA, the ticker "touched"
     the average. Of all touch days in the window, the one with the LOWEST
     close is recorded as *the* touch day (the deepest point of the pullback).
  3. `is_bounce` is only True if, in addition to having touched:
       (a) today's close is above today's 200 EMA, AND
       (b) today's close is higher than the close on the recorded touch day.
     This requires an actual upward reversal off a still-rising-ish average,
     not just "price happens to be near the average right now" and not a
     continued slide through it. It intentionally does NOT fire on the
     mirror-image case (price falling back down through the average from
     above, i.e. resistance rejection) -- only the "pullback to the average,
     then bounce upward" pattern.

Earnings definition:

  `earnings_recent` is True if the most recent PAST earnings date (from
  yfinance's get_earnings_dates) falls within the last `earnings_lookback_days`
  days (default 7). yfinance's earnings-date data is known to be inconsistent
  (sometimes tz-aware, sometimes not, sometimes empty/None, sometimes only
  future estimated dates) -- all of that is handled defensively below so a
  bad or missing earnings calendar for one ticker never crashes the scan.

Every per-ticker failure (network hiccup, delisted ticker, missing data) is
caught and logged; it never halts the rest of the scan.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("screener")

DEFAULT_PROXIMITY_PCT = 2.0
DEFAULT_EARNINGS_LOOKBACK_DAYS = 7
DEFAULT_BOUNCE_LOOKBACK_DAYS = 10


def _empty_result(ticker: str, error: str) -> dict:
    return {
        "ticker": ticker,
        "current_close": None,
        "ema200": None,
        "dist_pct": None,
        "is_bounce": False,
        "touched": False,
        "touch_date": None,
        "touch_close": None,
        "earnings_recent": False,
        "last_earnings_date": None,
        "error": error,
    }


def _naive_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Strip timezone info so date-math never raises tz-aware/naive comparison errors."""
    if getattr(idx, "tz", None) is not None:
        return idx.tz_convert("UTC").tz_localize(None)
    return idx


def _check_earnings(ticker_obj: yf.Ticker, lookback_days: int) -> tuple[bool, Optional[pd.Timestamp]]:
    """Return (earnings_recent, last_earnings_date). Never raises."""
    try:
        earnings_df = ticker_obj.get_earnings_dates(limit=12)
    except Exception as e:
        log.warning("get_earnings_dates failed: %s", e)
        return False, None

    if earnings_df is None or earnings_df.empty:
        return False, None

    try:
        idx = _naive_index(earnings_df.index)
        now = pd.Timestamp.utcnow().tz_localize(None)
        past = idx[idx <= now]
        if len(past) == 0:
            return False, None
        last_earnings_date = past.max()
        days_since = (now - last_earnings_date).days
        earnings_recent = 0 <= days_since <= lookback_days
        return earnings_recent, last_earnings_date
    except Exception as e:
        log.warning("earnings date normalization failed for %s: %s", ticker_obj.ticker, e)
        return False, None


def analyze_ticker(
    ticker: str,
    proximity_pct: float = DEFAULT_PROXIMITY_PCT,
    earnings_lookback_days: int = DEFAULT_EARNINGS_LOOKBACK_DAYS,
    bounce_lookback_days: int = DEFAULT_BOUNCE_LOOKBACK_DAYS,
) -> dict:
    """Analyze a single ticker. Always returns a result dict; never raises."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y", auto_adjust=True)

        if hist is None or hist.empty:
            return _empty_result(ticker, "no price history returned")

        hist = hist.dropna(subset=["Close"])
        if len(hist) < 200:
            return _empty_result(ticker, f"insufficient history ({len(hist)} rows, need >= 200)")

        ema200_series = hist["Close"].ewm(span=200, adjust=False).mean()

        today_close = float(hist["Close"].iloc[-1])
        today_ema = float(ema200_series.iloc[-1])
        dist_pct = (today_close - today_ema) / today_ema * 100.0

        window_n = min(bounce_lookback_days, len(hist))
        window_close = hist["Close"].iloc[-window_n:]
        window_ema = ema200_series.iloc[-window_n:]

        touched = False
        touch_date = None
        touch_close = None

        for date, close_val, ema_val in zip(window_close.index, window_close.values, window_ema.values):
            day_dist_pct = abs(float(close_val) - float(ema_val)) / float(ema_val) * 100.0
            if day_dist_pct <= proximity_pct:
                touched = True
                if touch_close is None or float(close_val) < touch_close:
                    touch_date = date
                    touch_close = float(close_val)

        is_bounce = bool(
            touched
            and today_close > today_ema
            and touch_close is not None
            and today_close > touch_close
        )

        earnings_recent, last_earnings_date = _check_earnings(t, earnings_lookback_days)

        return {
            "ticker": ticker,
            "current_close": round(today_close, 2),
            "ema200": round(today_ema, 2),
            "dist_pct": round(dist_pct, 2),
            "is_bounce": is_bounce,
            "touched": touched,
            "touch_date": touch_date.strftime("%Y-%m-%d") if touch_date is not None else None,
            "touch_close": round(touch_close, 2) if touch_close is not None else None,
            "earnings_recent": earnings_recent,
            "last_earnings_date": last_earnings_date.strftime("%Y-%m-%d") if last_earnings_date is not None else None,
            "error": None,
        }

    except Exception as e:
        log.exception("Failed to analyze %s", ticker)
        return _empty_result(ticker, str(e))


def scan_watchlist(
    tickers: list[str],
    proximity_pct: float = DEFAULT_PROXIMITY_PCT,
    earnings_lookback_days: int = DEFAULT_EARNINGS_LOOKBACK_DAYS,
    bounce_lookback_days: int = DEFAULT_BOUNCE_LOOKBACK_DAYS,
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
            )
        except Exception as e:
            log.exception("Unexpected top-level failure scanning %s", ticker)
            result = _empty_result(ticker, str(e))
        results.append(result)
    return results


if __name__ == "__main__":
    import json

    demo_tickers = ["AAPL", "MSFT", "NVDA"]
    print(json.dumps(scan_watchlist(demo_tickers), indent=2))
