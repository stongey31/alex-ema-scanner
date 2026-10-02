"""In-memory provider for tests and the dashboard's FAKE-data demo."""

from __future__ import annotations

import itertools
import string
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.providers.base import IntradayProvider, ProviderError, ProviderPermissionError
from core.scanner_base import ET


class FakeProvider(IntradayProvider):
    name = "fake"

    def __init__(self, snapshots: dict, bars: dict, configured: bool = True, permission_error_feeds=(), daily: Optional[dict] = None,
                 universe: Optional[list] = None, failing_snapshot_calls=()):
        self._snapshots = snapshots
        self._bars = bars
        self._daily = daily or {}
        self._configured = configured
        self.permission_error_feeds = tuple(permission_error_feeds)
        self.universe = list(universe) if universe is not None else []
        self.failing_snapshot_calls = set(failing_snapshot_calls)  # 0-based snapshot call numbers that raise ProviderError
        self.snapshot_sizes: list[int] = []
        self.calls: list[tuple] = []

    def list_symbols(self, base_url=None):
        self.calls.append(("list_symbols",))
        return list(self.universe)

    def is_configured(self) -> bool:
        return self._configured

    def snapshots(self, symbols, feed="iex"):
        self.calls.append(("snapshots", feed))
        n = len(self.snapshot_sizes)
        self.snapshot_sizes.append(len(symbols))
        if n in self.failing_snapshot_calls:
            raise ProviderError("simulated chunk failure")
        if feed in self.permission_error_feeds:
            raise ProviderPermissionError("feed not allowed")
        return {s: self._snapshots[s] for s in symbols if s in self._snapshots}

    def bars(self, symbols, start, end, timeframe="5Min", feed="sip", adjustment="raw"):
        self.calls.append(("bars", timeframe, feed))
        if feed in self.permission_error_feeds:
            raise ProviderPermissionError("feed not allowed")
        if timeframe == "1Day":
            return {s: self._daily[s] for s in symbols if s in self._daily}
        out = {}
        for s in symbols:
            df = self._bars.get(s)
            if df is None:
                continue
            out[s] = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))] if start and end else df
        return out


def make_premarket_bars(
    now_et: datetime,
    today_volume: float,
    hist_volume: float,
    sessions: int = 10,
    start: str = "04:00",
    price: float = 10.0,
    bar_minutes: int = 5,
    full_until: str = "09:30",
) -> pd.DataFrame:
    """Synthetic pre-market bars: `sessions` past weekdays plus today (up to
    `full_until` for past days, up to now_et for today). Volume is spread
    evenly over the bars, so a session's volume up to time T is proportional to
    the bars before T. today_volume/hist_volume are the totals over the
    04:00 -> `now_et` clock window (so rel_volume ~= today_volume/hist_volume
    when computed at that same time).
    """
    sh, sm = (int(x) for x in start.split(":"))
    today = now_et.date()
    days = []
    d = today - timedelta(days=1)
    while len(days) < sessions:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days = sorted(days)

    def clock_bars(day, end_h, end_m):
        t = datetime(day.year, day.month, day.day, sh, sm, tzinfo=ET)
        end = datetime(day.year, day.month, day.day, end_h, end_m, tzinfo=ET)
        out = []
        while t < end:
            out.append(t)
            t += timedelta(minutes=bar_minutes)
        return out

    window_end = (now_et.hour, now_et.minute)
    n_window = len(clock_bars(today, *window_end)) or 1
    fh, fm = (int(x) for x in full_until.split(":"))
    rng = np.random.default_rng(1)
    rows, idx = [], []
    for day in days + [today]:
        if day == today:
            times, per = clock_bars(day, *window_end), today_volume / n_window
        else:
            times, per = clock_bars(day, fh, fm), hist_volume / n_window
        for t in times:
            p = price * (1 + rng.normal(0, 0.001))
            rows.append({"open": p, "high": p * 1.002, "low": p * 0.998, "close": p, "volume": per})
            idx.append(t)
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    return df


def make_demo_provider(tickers: list[str], now_et: datetime) -> "FakeProvider":
    """FAKE data for the dashboard demo: the first ~third of tickers look like
    real hits (big gap, heavy volume), the rest are quiet."""
    snapshots, bars, daily = {}, {}, {}
    n_hits = max(1, len(tickers) // 3)
    for i, t in enumerate(tickers):
        hit = i < n_hits
        base = 5.0 + 3 * i
        prev_close = base
        price = base * (1.12 + 0.01 * i) if hit else base * 1.005
        today_vol = 900_000 if hit else 60_000
        bars[t] = make_premarket_bars(now_et, today_vol, 100_000, price=price)
        snapshots[t] = {"last_price": price, "last_trade_time": now_et}
        d = now_et.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        daily[t] = pd.DataFrame(
            {"open": prev_close, "high": prev_close, "low": prev_close, "close": prev_close, "volume": 1e6},
            index=pd.DatetimeIndex([d]),
        )
    return FakeProvider(snapshots, bars, daily=daily)


def make_demo_universe_provider(now_et: datetime, n_symbols: int = 300, n_hits: int = 3, n_thin_gappers: int = 3) -> "FakeProvider":
    """FAKE whole-market demo: ~n_symbols made-up tickers, a few real-looking hits
    (10%+ gap on heavy volume), a few gappers on thin volume, the rest quiet."""
    symbols = ["".join(p) for p in itertools.islice(itertools.product(string.ascii_uppercase, repeat=3), n_symbols)]
    prev_day = (now_et - timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
    day_before = prev_day - timedelta(days=1)
    snapshots, bars, daily = {}, {}, {}
    for i, t in enumerate(symbols):
        prev_close = 5.0 + (i % 40)
        hit, thin = i < n_hits, n_hits <= i < n_hits + n_thin_gappers
        price = prev_close * (1.15 + 0.02 * i) if hit else prev_close * 1.12 if thin else prev_close * 1.01
        snapshots[t] = {
            "last_price": price,
            "last_trade_time": now_et - timedelta(minutes=2),
            "dailyBar": {"t": prev_day.isoformat(), "c": prev_close},
            "prevDailyBar": {"t": day_before.isoformat(), "c": prev_close},
        }
        if hit or thin:
            bars[t] = make_premarket_bars(now_et, 900_000 if hit else 60_000, 100_000, price=price)
            d = prev_day.replace(hour=0)
            daily[t] = pd.DataFrame(
                {"open": prev_close, "high": prev_close, "low": prev_close, "close": prev_close, "volume": 1e6},
                index=pd.DatetimeIndex([d]),
            )
    return FakeProvider(snapshots, bars, daily=daily, universe=symbols)
