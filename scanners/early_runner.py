"""Early Runner Setup: small, low-float stocks breaking out on huge volume.

Looks for the early stage of the kind of move that can turn a tiny stock into a
big winner (the profile in the notes: small market cap, small float, a sudden
volume surge and a price breakout that holds above the short-term averages).
It finds stocks that LOOK like past runners at the start. It cannot know
whether a catalyst exists, and most flags will fizzle -- treat each one as a
reason to research, not a buy signal.

Runs once a day after the close over the whole US market, in three stages so
the number of calls stays small:

  1. Sweep: one IEX snapshot call per 500 symbols; keep stocks that closed up
     at least `stage1_min_move_pct` and cost at least `min_price`.
  2. Trend and volume (daily bars for the survivors only): flagged when ALL of
       * today's volume >= `min_rel_volume` x the average of the prior
         `avg_volume_days` sessions,
       * the close is above the highest high of the prior `breakout_days`
         sessions (a breakout), and
       * the close is above both the fast and slow EMA.
  3. Size (yfinance, only for stocks that passed stage 2, strongest first):
     market cap below `max_market_cap_m` ($ millions) AND float below
     `max_float_m` (millions of shares). Short interest is shown as a bonus.
     If yfinance has no data for a stock its size is "unknown": it appears in
     the table but is NOT flagged and sends no alert.

Honest limits: the free Alpaca plan only reports IEX volume (a small slice of
real volume), so volume ratios are meaningful but absolute volume is not.
Float and market cap come from yfinance (free, unofficial, sometimes missing).
Revenue growth and the news catalyst cannot be scanned.
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Optional

import pandas as pd

from core.config import resolve_tickers
from core.providers.alpaca import NOT_CONFIGURED_MSG, AlpacaProvider
from core.providers.base import ProviderAuthError, ProviderError, ProviderPermissionError
from core.scanner_base import RunContext, ScanOutput, Scanner, Signal

log = logging.getLogger("early_runner")

SNAPSHOT_CHUNK = 500
EPISODE_SESSIONS = 10  # a stock flagged again within ~10 sessions counts as the same episode


def _num(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _bar_date(bar) -> Optional[str]:
    if not isinstance(bar, dict) or not bar.get("t"):
        return None
    try:
        return pd.Timestamp(bar["t"]).tz_convert("America/New_York").date().isoformat()
    except (ValueError, TypeError):
        return None


def stage1_candidates(snaps: dict, cfg: dict) -> list[dict]:
    """Cheap sweep on snapshots: price floor and a minimum one-day move. Ranked by move, biggest first."""
    out = []
    for sym, snap in (snaps or {}).items():
        try:
            day, prev = (snap or {}).get("dailyBar"), (snap or {}).get("prevDailyBar")
            close, prev_close = _num((day or {}).get("c")), _num((prev or {}).get("c"))
            if close is None or prev_close is None or prev_close <= 0 or close < float(cfg["min_price"]):
                continue
            move = (close - prev_close) / prev_close * 100.0
            if move >= float(cfg["stage1_min_move_pct"]):
                out.append({"ticker": sym, "close": close, "move_pct": move, "bar_date": _bar_date(day)})
        except Exception:  # one odd snapshot must not stop the sweep
            continue
    out.sort(key=lambda c: c["move_pct"], reverse=True)
    return out


def evaluate_bars(ticker: str, df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """Trend/volume metrics from daily bars (last bar = the session being scanned). None if there is too little history."""
    breakout_days = int(cfg["breakout_days"])
    avg_days = int(cfg["avg_volume_days"])
    if df is None or len(df) < breakout_days + 1:
        return None
    df = df.dropna(subset=["close", "high", "low", "volume"])
    if len(df) < breakout_days + 1:
        return None
    today, prior = df.iloc[-1], df.iloc[:-1]
    avg_vol = float(prior["volume"].tail(avg_days).mean())
    close = float(today["close"])
    volume = float(today["volume"])
    prior_high = float(prior["high"].tail(breakout_days).max())
    base = prior.tail(int(cfg["base_days"]))
    base_low = float(base["low"].min())
    ema_fast = float(df["close"].ewm(span=int(cfg["ema_fast"]), adjust=False).mean().iloc[-1])
    ema_slow = float(df["close"].ewm(span=int(cfg["ema_slow"]), adjust=False).mean().iloc[-1])
    prev_close = float(prior["close"].iloc[-1])
    rel_volume = volume / avg_vol if avg_vol > 0 else None
    return {
        "ticker": ticker,
        "close": round(close, 4),
        "change_pct": round((close - prev_close) / prev_close * 100.0, 2) if prev_close > 0 else None,
        "volume": volume,
        "avg_volume": round(avg_vol, 0),
        "rel_volume": round(rel_volume, 2) if rel_volume is not None else None,
        "prior_high": round(prior_high, 4),
        "above_prior_high_pct": round((close - prior_high) / prior_high * 100.0, 2) if prior_high > 0 else None,
        "ema_fast": round(ema_fast, 4),
        "ema_slow": round(ema_slow, 4),
        "base_range_pct": round((float(base["high"].max()) / base_low - 1) * 100.0, 1) if base_low > 0 else None,
        "bar_date": df.index[-1].date().isoformat(),
        "episode_start": df.index[max(0, len(df) - EPISODE_SESSIONS)].date().isoformat(),
    }


def trend_passes(m: dict, cfg: dict) -> bool:
    return bool(
        m["rel_volume"] is not None
        and m["rel_volume"] >= float(cfg["min_rel_volume"])
        and m["volume"] >= float(cfg["min_volume"])
        and m["close"] > m["prior_high"]
        and m["close"] > m["ema_fast"]
        and m["close"] > m["ema_slow"]
    )


def size_check(market_cap: Optional[float], float_shares: Optional[float], cfg: dict) -> Optional[bool]:
    """True = small enough, False = too big, None = couldn't tell (missing data)."""
    checks = []
    if market_cap is not None:
        checks.append(market_cap < float(cfg["max_market_cap_m"]) * 1e6)
    if float_shares is not None:
        checks.append(float_shares < float(cfg["max_float_m"]) * 1e6)
    if any(c is False for c in checks):
        return False
    return True if len(checks) == 2 else None


class EarlyRunnerScanner(Scanner):
    name = "Early Runner Setup"
    description = (
        "Whole-market scan after the close for small, low-float stocks breaking out to a new high on unusually heavy "
        "volume while above their short-term averages. Early-stage setups only: most will fizzle, so use as a research list."
    )
    lane = "daily"
    default_config = {
        "universe_mode": "whole_market",  # or "watchlist" (uses "watchlist" below)
        "watchlist": "momentum",
        "assets_base_url": "https://paper-api.alpaca.markets",
        "price_feed": "iex",
        "min_price": 1.0,
        "stage1_min_move_pct": 3.0,
        "max_candidates": 1500,
        "min_rel_volume": 5.0,
        "min_volume": 10000,  # IEX shares only (a small slice of real volume)
        "avg_volume_days": 30,
        "breakout_days": 60,
        "ema_fast": 10,
        "ema_slow": 20,
        "base_days": 90,
        "max_market_cap_m": 500,
        "max_float_m": 25,
        "fundamentals_check_max": 60,
    }
    setting_meta = {
        "min_price": {"label": "Minimum price ($)", "min": 0.0, "max": 1000.0, "step": 0.5},
        "stage1_min_move_pct": {"label": "First-pass minimum move today (%)", "help": "Cheap filter before the volume and trend checks", "min": 0.0, "max": 50.0, "step": 0.5},
        "max_candidates": {"label": "Max movers to check", "min": 50, "max": 5000, "step": 50},
        "min_rel_volume": {"label": "Minimum relative volume (x)", "help": "Today's volume vs. the prior-30-day average", "min": 1.0, "max": 100.0, "step": 0.5},
        "min_volume": {"label": "Minimum IEX volume today", "help": "IEX is only a slice of real volume", "min": 0, "max": 100000000, "step": 1000},
        "avg_volume_days": {"label": "Average-volume window (sessions)", "min": 5, "max": 90, "step": 1},
        "breakout_days": {"label": "Breakout window (sessions)", "help": "Close must beat the highest high of this many prior sessions", "min": 10, "max": 120, "step": 5},
        "ema_fast": {"label": "Fast EMA (days)", "min": 3, "max": 50, "step": 1},
        "ema_slow": {"label": "Slow EMA (days)", "min": 5, "max": 100, "step": 1},
        "base_days": {"label": "Base window for the 'quiet base' column (sessions)", "min": 20, "max": 250, "step": 5},
        "max_market_cap_m": {"label": "Maximum market cap ($ millions)", "min": 1, "max": 100000, "step": 25},
        "max_float_m": {"label": "Maximum float (millions of shares)", "min": 1, "max": 1000, "step": 1},
        "fundamentals_check_max": {"label": "Max stocks to size-check (market cap / float)", "min": 5, "max": 200, "step": 5},
    }
    column_formats = {
        "close": "${:.2f}",
        "change_pct": "{:+.2f}%",
        "volume": "{:,.0f}",
        "avg_volume": "{:,.0f}",
        "rel_volume": "{:.1f}x",
        "prior_high": "${:.2f}",
        "above_prior_high_pct": "{:+.1f}%",
        "ema_fast": "${:.2f}",
        "ema_slow": "${:.2f}",
        "base_range_pct": "{:.0f}%",
        "market_cap_m": "${:,.0f}M",
        "float_m": "{:.1f}M",
        "short_pct_float": "{:.1f}%",
    }
    candidates_label = "Passed volume and trend"
    whole_market_caption = "Scanning the whole market: biggest movers first, then a volume/trend check, then a size check on the survivors."

    # -- data ----------------------------------------------------------------

    def _fundamentals(self, ticker: str) -> dict:
        """market cap ($), float (shares), short interest (% of float) from yfinance; None for anything missing."""
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        short = _num(info.get("shortPercentOfFloat"))
        return {
            "market_cap": _num(info.get("marketCap")),
            "float_shares": _num(info.get("floatShares")),
            "short_pct_float": round(short * 100.0, 1) if short is not None else None,
        }

    def _sweep(self, provider, universe: list[str], cfg: dict):
        snaps, failed, ok = {}, 0, 0
        for i in range(0, len(universe), SNAPSHOT_CHUNK):
            chunk = universe[i : i + SNAPSHOT_CHUNK]
            try:
                snaps.update(provider.snapshots(chunk, feed=cfg["price_feed"]))
                ok += 1
            except (ProviderAuthError, ProviderPermissionError):
                raise
            except ProviderError as e:
                log.warning("Snapshot chunk %d failed: %s", i // SNAPSHOT_CHUNK, e)
                failed += 1
        if failed and not ok:
            raise ProviderError("Could not fetch market snapshots from Alpaca")
        return snaps, failed

    # -- the scan ------------------------------------------------------------

    def run(self, tickers, config, ctx: RunContext) -> ScanOutput:
        cfg = {**self.default_config, **config}
        provider = ctx.intraday_provider or AlpacaProvider()
        if not provider.is_configured():
            return ScanOutput("not_configured", NOT_CONFIGURED_MSG)
        now_et = ctx.now

        try:
            if cfg.get("universe_mode", "whole_market") == "watchlist":
                universe = list(tickers)
            else:
                universe = provider.list_symbols(cfg.get("assets_base_url"))
            snaps, failed = self._sweep(provider, universe, cfg)
            cands_all = stage1_candidates(snaps, cfg)
            cands = cands_all[: int(cfg["max_candidates"])]
            head = f"Swept {len(universe):,} symbols; {len(cands_all)} moved >={cfg['stage1_min_move_pct']:g}%"
            if len(cands_all) > len(cands):
                head += f" (checked the top {len(cands)})"
            if failed:
                head += f"; {failed} snapshot chunk(s) failed and were skipped"
            extra = {"swept": len(universe), "candidates_label": self.candidates_label}
            if not cands:
                return ScanOutput("ok", f"{head}; 0 flagged", [], [], extra=extra)
            start = now_et - timedelta(days=max(200, int(cfg["base_days"]) * 2 + 30))
            bars = provider.bars([c["ticker"] for c in cands], start, now_et, timeframe="1Day", feed=cfg["price_feed"], adjustment="split")
        except ProviderError as e:  # includes auth errors; messages never contain keys
            return ScanOutput("error", str(e))

        passed = []
        for c in cands:
            try:
                m = evaluate_bars(c["ticker"], bars.get(c["ticker"]), cfg)
                if m is None:
                    continue
                if c["bar_date"] and m["bar_date"] != c["bar_date"]:
                    continue  # daily bars lag the snapshot; skip rather than score the wrong day
                if trend_passes(m, cfg):
                    passed.append(m)
            except Exception as e:
                log.warning("Failed to evaluate %s: %s", c["ticker"], type(e).__name__)
        passed.sort(key=lambda m: m["rel_volume"], reverse=True)

        rows, signals = [], []
        limit = int(cfg["fundamentals_check_max"])
        for i, m in enumerate(passed):
            row = {
                **{k: v for k, v in m.items() if k not in ("bar_date", "episode_start")},
                "market_cap_m": None, "float_m": None, "short_pct_float": None,
                "size": "not checked" if i >= limit else "unknown", "flagged": False, "error": None,
            }
            if i < limit:
                try:
                    f = self._fundamentals(m["ticker"])
                    if f["market_cap"] is not None:
                        row["market_cap_m"] = round(f["market_cap"] / 1e6, 1)
                    if f["float_shares"] is not None:
                        row["float_m"] = round(f["float_shares"] / 1e6, 2)
                    row["short_pct_float"] = f["short_pct_float"]
                    ok = size_check(f["market_cap"], f["float_shares"], cfg)
                    row["size"] = {True: "small enough", False: "too big", None: "unknown"}[ok]
                    row["flagged"] = ok is True
                except Exception as e:  # yfinance is flaky; never stop the scan
                    log.warning("Fundamentals failed for %s: %s", m["ticker"], type(e).__name__)
                    row["error"] = "size data unavailable"
            rows.append(row)
            if row["flagged"]:
                signals.append(
                    Signal(
                        m["ticker"], f"{m['ticker']}:early_runner", m["bar_date"], m["episode_start"],
                        data={k: row[k] for k in ("close", "change_pct", "rel_volume", "above_prior_high_pct", "market_cap_m", "float_m", "short_pct_float")},
                    )
                )
        extra["flagged"] = len(signals)
        message = f"{head}; {len(passed)} passed volume and trend; {len(signals)} flagged"
        return ScanOutput("ok", message, rows, signals, extra=extra)

    # -- alerts ----------------------------------------------------------------

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
                short = d.get("short_pct_float")
                lines = [
                    f"RelVol {d.get('rel_volume', 0):.1f}x | {d.get('change_pct', 0):+.1f}% today | ${d.get('close', 0):,.2f}",
                    f"Cap ${d.get('market_cap_m', 0):,.0f}M | Float {d.get('float_m', 0):.1f}M"
                    + (f" | Short {short:.1f}% of float" if short is not None else ""),
                    f"[Chart](https://www.tradingview.com/symbols/{ticker})",
                ]
                fields.append({"name": ticker, "value": "\n".join(lines), "inline": False})
            embed = {
                "title": f"Early runner setup: {len(chunk)} ticker(s)",
                "description": "Small cap, low float, breakout on heavy volume. A research list, not a buy signal.",
                "color": 0x2ECC71,
                "fields": fields,
            }
            out.append(({"embeds": [embed]}, [s for _t, sigs in chunk for s in sigs]))
        return out
