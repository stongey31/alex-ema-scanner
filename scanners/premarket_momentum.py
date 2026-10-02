"""Pre-market momentum scanner: unusual pre-market volume plus a big gap up.

RelVol (relative volume) = today's cumulative volume from the session start
(default 04:00 ET) to a cutoff time T, divided by the AVERAGE cumulative volume
over the same clock window on each of the previous `lookback_days` sessions.
T is "now" minus `volume_delay_minutes` (the free Alpaca plan delays full-market
volume by ~15 minutes), so today and history are always compared up to the
same clock time.

Gap % = (latest price - previous close) / previous close * 100.

A ticker is flagged when RelVol, gap, price and raw volume all clear their
minimums (see default_config).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from core.providers.alpaca import AlpacaProvider
from core.providers.base import ProviderAuthError, ProviderError, ProviderPermissionError
from core.scanner_base import RunContext, ScanOutput, Scanner, Signal

log = logging.getLogger("premarket_momentum")

NOT_CONFIGURED_MSG = (
    "Alpaca API keys are not set up yet. Add ALPACA_API_KEY and ALPACA_API_SECRET "
    "(see README, 'Turning on the pre-market scanner')."
)
FALLBACK_MSG = "Full-market volume not available on this plan; using IEX-only volume (thin, noisy)"


def _parse_hhmm(text: str):
    h, m = str(text).split(":")
    return int(h), int(m)


def _clock_minutes(index: pd.DatetimeIndex):
    return index.hour * 60 + index.minute


def _empty_row(ticker: str, cfg: dict, error: Optional[str], **kw) -> dict:
    row = {
        "ticker": ticker,
        "last_price": None,
        "prev_close": None,
        "gap_pct": None,
        "volume_so_far": None,
        "avg_volume_same_time": None,
        "rel_volume": None,
        "volume_as_of": None,
        "volume_feed_used": cfg.get("volume_feed"),
        "flagged": False,
        "error": error,
    }
    row.update(kw)
    return row


def compute_row(ticker: str, last_price, prev_close, bars_df, now_et: datetime, cfg: dict) -> dict:
    """Pure function: one ticker's pre-market numbers. Never touches the network."""
    cutoff = now_et - timedelta(minutes=cfg["volume_delay_minutes"])
    t_min = cutoff.hour * 60 + cutoff.minute
    s_h, s_m = _parse_hhmm(cfg["session_start_et"])
    s_min = s_h * 60 + s_m
    as_of = cutoff.strftime("%H:%M")
    today = now_et.date()

    if bars_df is None or len(bars_df) == 0:
        return _empty_row(ticker, cfg, "no intraday bars", last_price=last_price, prev_close=prev_close, volume_as_of=as_of)

    df = bars_df
    mins = _clock_minutes(df.index)
    in_window = (mins >= s_min) & (mins < t_min)
    dates = pd.Index(df.index.date)

    today_vol = float(df["volume"][in_window & (dates == today)].sum())

    past_dates = sorted({d for d in dates if d < today}, reverse=True)[: int(cfg["lookback_days"])]
    if len(past_dates) < int(cfg["min_history_days"]):
        return _empty_row(
            ticker, cfg, "not enough history",
            last_price=last_price, prev_close=prev_close, volume_so_far=today_vol, volume_as_of=as_of,
        )
    hist_vols = [float(df["volume"][in_window & (dates == d)].sum()) for d in past_dates]
    avg = sum(hist_vols) / len(hist_vols)
    rel = today_vol / avg if avg > 0 else None

    gap = None
    if last_price is not None and prev_close is not None and last_price > 0 and prev_close > 0:
        gap = (last_price - prev_close) / prev_close * 100.0

    flagged = bool(
        rel is not None
        and gap is not None
        and last_price is not None
        and rel >= cfg["min_rel_volume"]
        and gap >= cfg["min_gap_pct"]
        and last_price >= cfg["min_price"]
        and today_vol >= cfg["min_volume"]
    )
    return _empty_row(
        ticker, cfg, None,
        last_price=last_price,
        prev_close=prev_close,
        gap_pct=round(gap, 2) if gap is not None else None,
        volume_so_far=today_vol,
        avg_volume_same_time=round(avg, 1),
        rel_volume=round(rel, 2) if rel is not None else None,
        volume_as_of=as_of,
        flagged=flagged,
    )


class PremarketMomentumScanner(Scanner):
    name = "Pre-market Momentum"
    description = (
        "Flags stocks gapping up pre-market on unusually heavy volume (RelVol = today's volume so far "
        "divided by the average volume at the same time of day over recent sessions)."
    )
    lane = "intraday"
    default_config = {
        "watchlist": "momentum",
        "min_rel_volume": 5.0,
        "min_gap_pct": 10.0,
        "min_price": 1.0,
        "min_volume": 50000,
        "lookback_days": 10,
        "min_history_days": 5,
        "session_start_et": "04:00",
        "bar_timeframe": "5Min",
        "price_feed": "iex",
        "volume_feed": "sip",
        "volume_delay_minutes": 16,
        "auto_refresh_seconds": 60,
    }
    setting_meta = {
        "min_rel_volume": {"label": "Minimum RelVol (x)", "help": "Today's volume vs. the same-time average", "min": 1.0, "max": 100.0, "step": 0.5},
        "min_gap_pct": {"label": "Minimum gap (%)", "help": "Price vs. yesterday's close", "min": 0.0, "max": 100.0, "step": 0.5},
        "min_price": {"label": "Minimum price ($)", "min": 0.0, "max": 1000.0, "step": 0.5},
        "min_volume": {"label": "Minimum volume so far", "min": 0, "max": 100000000, "step": 10000},
        "lookback_days": {"label": "History sessions to average", "min": 2, "max": 30, "step": 1},
        "min_history_days": {"label": "Minimum sessions of history", "min": 1, "max": 30, "step": 1},
        "volume_delay_minutes": {"label": "Volume delay (minutes)", "help": "15-16 on the free Alpaca plan, 0 on a paid plan", "min": 0, "max": 60, "step": 1},
        "auto_refresh_seconds": {"label": "Auto-refresh (seconds)", "min": 15, "max": 600, "step": 15},
    }
    column_formats = {
        "last_price": "${:.2f}",
        "prev_close": "${:.2f}",
        "gap_pct": "{:+.2f}%",
        "volume_so_far": "{:,.0f}",
        "avg_volume_same_time": "{:,.0f}",
        "rel_volume": "{:.2f}x",
    }

    # -- data fetching -------------------------------------------------------

    def _fetch(self, provider, symbols, cfg, now_et, volume_feed, delay):
        today = now_et.date()
        cutoff = now_et - timedelta(minutes=delay)
        tz = now_et.tzinfo
        s_h, s_m = _parse_hhmm(cfg["session_start_et"])
        start_day = today - timedelta(days=math.ceil(int(cfg["lookback_days"]) * 1.6) + 5)
        start = datetime(start_day.year, start_day.month, start_day.day, s_h, s_m, tzinfo=tz)
        daily = provider.bars(
            symbols, now_et - timedelta(days=10), cutoff, timeframe="1Day", feed=volume_feed, adjustment="split"
        )
        intraday = provider.bars(
            symbols, start, cutoff, timeframe=cfg["bar_timeframe"], feed=volume_feed, adjustment="raw"
        )
        return daily, intraday

    @staticmethod
    def _prev_close(daily_df, intraday_df, today):
        if daily_df is not None and len(daily_df):
            before = daily_df[pd.Index(daily_df.index.date) < today]
            if len(before):
                return float(before["close"].iloc[-1])
        # Fallback: last close of the most recent earlier session in the intraday bars
        # (only covers the pre-market window, so it is an approximation).
        if intraday_df is not None and len(intraday_df):
            before = intraday_df[pd.Index(intraday_df.index.date) < today]
            if len(before):
                return float(before["close"].iloc[-1])
        return None

    def run(self, tickers, config, ctx: RunContext) -> ScanOutput:
        cfg = {**self.default_config, **config}
        provider = ctx.intraday_provider or AlpacaProvider()
        if not provider.is_configured():
            return ScanOutput("not_configured", NOT_CONFIGURED_MSG)

        now_et = ctx.now
        today = now_et.date()
        message = ""
        try:
            snaps = provider.snapshots(tickers, feed=cfg["price_feed"])
            volume_feed, delay = cfg["volume_feed"], cfg["volume_delay_minutes"]
            try:
                daily, intraday = self._fetch(provider, tickers, cfg, now_et, volume_feed, delay)
            except ProviderPermissionError:
                volume_feed, delay = "iex", 0
                message = FALLBACK_MSG
                daily, intraday = self._fetch(provider, tickers, cfg, now_et, volume_feed, delay)
        except ProviderAuthError as e:
            return ScanOutput("error", str(e))
        except ProviderError as e:
            return ScanOutput("error", str(e))

        row_cfg = {**cfg, "volume_feed": volume_feed, "volume_delay_minutes": delay}
        rows, signals, today_bars = [], [], {}
        for t in tickers:
            try:
                snap = snaps.get(t) or {}
                price = snap.get("last_price")
                bars_df = intraday.get(t)
                prev_close = self._prev_close(daily.get(t), bars_df, today)
                if price is None:
                    rows.append(_empty_row(t, row_cfg, "no price data"))
                    continue
                row = compute_row(t, price, prev_close, bars_df, now_et, row_cfg)
            except Exception as e:  # never let one ticker stop the scan
                log.warning("Failed to evaluate %s: %s", t, type(e).__name__)
                row = _empty_row(t, row_cfg, f"{type(e).__name__}: {e}")
            rows.append(row)
            if row["flagged"]:
                signals.append(
                    Signal(
                        t, f"{t}:gap", today.isoformat(), today.isoformat(),
                        data={"gap": row["gap_pct"], "relvol": row["rel_volume"], "price": row["last_price"]},
                    )
                )
                if bars_df is not None:
                    today_bars[t] = bars_df[pd.Index(bars_df.index.date) == today]
        return ScanOutput("ok", message, rows, signals, extra={"bars": today_bars})

    # -- alerts / UI -----------------------------------------------------------

    def format_alert(self, ticker, signals, row) -> dict:
        return self.build_alert_messages({ticker: signals}, {ticker: row or {}})[0][0]

    def build_alert_messages(self, hits, rows_by_ticker):
        items = list(hits.items())
        out = []
        for i in range(0, len(items), 25):
            chunk = items[i : i + 25]
            fields = []
            for ticker, sigs in chunk:
                d = sigs[0].data if sigs else {}
                gap, rel, price = d.get("gap"), d.get("relvol"), d.get("price")
                fields.append(
                    {
                        "name": ticker,
                        "value": (
                            f"Gap {gap:+.1f}% | RelVol {rel:.1f}x | ${price:,.2f}\n"
                            f"[Chart](https://www.tradingview.com/symbols/{ticker})"
                        ),
                        "inline": False,
                    }
                )
            embed = {
                "title": f"Pre-market momentum: {len(chunk)} ticker(s)",
                "description": "High relative volume and a big gap up before the open.",
                "color": 0xF1C40F,
                "fields": fields,
            }
            out.append(({"embeds": [embed]}, [s for _t, sigs in chunk for s in sigs]))
        return out

    def render_detail(self, output: ScanOutput, config: dict) -> None:
        import streamlit as st

        from core.charts import intraday_candles

        bars = (output.extra or {}).get("bars") or {}
        flagged = [r["ticker"] for r in output.rows if r.get("flagged") and r["ticker"] in bars]
        if not flagged:
            return
        st.subheader("Intraday chart")
        sel = st.selectbox("Flagged ticker", flagged, key="detail_premarket_momentum")
        row = next(r for r in output.rows if r["ticker"] == sel)
        hl = [(row["prev_close"], "prev close")] if row.get("prev_close") else []
        st.plotly_chart(intraday_candles(bars[sel], f"{sel} -- today's bars", hl), width="stretch")
