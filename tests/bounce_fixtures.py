"""Synthetic price histories + a fake yfinance.Ticker for the bounce equivalence tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

N_DAYS = 520


def _index(n: int = N_DAYS) -> pd.DatetimeIndex:
    end = pd.Timestamp.now(tz="America/New_York").normalize()
    return pd.bdate_range(end=end, periods=n, tz="America/New_York")


def ohlcv(close: np.ndarray, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = np.asarray(close, dtype=float)
    open_ = close * (1 + rng.normal(0, 0.002, len(close)))
    high = np.maximum(open_, close) * 1.003
    low = np.minimum(open_, close) * 0.997
    vol = rng.integers(1_000_000, 5_000_000, len(close)).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=_index(len(close))
    )


def random_walk(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0003, 0.0004)
    vol = rng.uniform(0.008, 0.025)
    rets = rng.normal(drift, vol, N_DAYS)
    return ohlcv(100 * np.exp(np.cumsum(rets)), seed)


def pullback_series(
    trend_pct: float, down_days: int, down_pct: float, flat_days: int, up_days: int, up_pct: float, seed: int = 1
) -> pd.DataFrame:
    """Steady uptrend, a sharp drop, an optional flat stretch, then a bounce.

    All moves are daily percentage changes; the last `up_days` closes rise.
    """
    rng = np.random.default_rng(seed)
    n_trend = N_DAYS - down_days - flat_days - up_days
    rets = np.concatenate(
        [
            rng.normal(trend_pct, 0.002, n_trend),
            np.full(down_days, -down_pct),
            np.full(flat_days, 0.0),
            np.full(up_days, up_pct),
        ]
    )
    return ohlcv(100 * np.cumprod(1 + rets), seed)


class FakeTicker:
    """Stand-in for yfinance.Ticker driven by a registry of per-symbol cases."""

    registry: dict = {}

    def __init__(self, symbol: str):
        self.ticker = symbol
        self._case = FakeTicker.registry[symbol]

    def history(self, period="2y", auto_adjust=True):
        h = self._case["history"]
        if isinstance(h, Exception):
            raise h
        return None if h is None else h.copy()

    def get_earnings_dates(self, limit=12):
        e = self._case.get("earnings", None)
        if isinstance(e, Exception):
            raise e
        return e


def earnings_frame(days_ago: list[int], days_ahead: list[int] = (), tz="America/New_York") -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    stamps = [now - pd.Timedelta(days=d) for d in days_ago] + [now + pd.Timedelta(days=d) for d in days_ahead]
    idx = pd.DatetimeIndex(stamps)
    idx = idx.tz_convert(tz) if tz else idx.tz_localize(None)
    return pd.DataFrame({"EPS Estimate": np.nan}, index=idx.sort_values(ascending=False))
