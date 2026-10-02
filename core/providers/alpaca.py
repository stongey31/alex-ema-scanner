"""Alpaca market-data provider using plain `requests` (no alpaca-py)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

from core.providers.base import IntradayProvider, ProviderAuthError, ProviderError, ProviderPermissionError
from core.scanner_base import ET
from core.secrets import get_secret

BASE_URL = "https://data.alpaca.markets"
ASSETS_BASE_URL = "https://paper-api.alpaca.markets"  # Trading API host: the assets list lives here, not on the data host
CHUNK = 100  # bars
SNAPSHOT_CHUNK = 500
MAX_RETRIES = 3
ASSETS_TTL_SECONDS = 12 * 3600
EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}

# base_url -> (fetched_at, symbols). Module-level so Streamlit reruns share it.
_ASSETS_CACHE: dict[str, tuple[float, list[str]]] = {}

NOT_CONFIGURED_MSG = (
    "Alpaca API keys are not set up yet. Add ALPACA_API_KEY and ALPACA_API_SECRET "
    "(see README, 'Turning on the pre-market scanner')."
)


def filter_assets(assets) -> list[str]:
    """Tradable plain-ticker US stocks: drops warrants/rights/units (dots, digits) and odd exchanges."""
    out = set()
    for a in assets or []:
        try:
            sym = str(a.get("symbol") or "").upper()
            if a.get("tradable") and a.get("exchange") in EXCHANGES and sym.isalpha() and sym.isascii() and len(sym) <= 5:
                out.add(sym)
        except AttributeError:
            continue
    return sorted(out)


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AlpacaProvider(IntradayProvider):
    name = "alpaca"

    def __init__(self, key: Optional[str] = None, secret: Optional[str] = None):
        self._key = key if key is not None else get_secret("ALPACA_API_KEY")
        self._secret = secret if secret is not None else get_secret("ALPACA_API_SECRET")

    def is_configured(self) -> bool:
        return bool(self._key and self._secret)

    def _get(self, path: str, params: dict, base: str = BASE_URL):
        headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret}
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.get(base.rstrip("/") + path, params=params, headers=headers, timeout=10)
            except requests.RequestException as e:
                # Don't include the exception text: it can echo request details.
                raise ProviderError(f"Could not reach Alpaca ({type(e).__name__})") from None
            if resp.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(2**attempt)
                continue
            break
        if resp.status_code == 401:
            raise ProviderAuthError("Alpaca rejected the API keys")
        if resp.status_code in (403, 422):
            text = (resp.text or "")[:300]
            low = text.lower()
            if resp.status_code == 403 and "forbidden" in low and not any(w in low for w in ("subscription", "permission", "sip")):
                raise ProviderAuthError("Alpaca rejected the API keys")
            if any(w in low for w in ("subscription", "permission", "sip")):
                raise ProviderPermissionError("Alpaca plan does not allow this data feed")
            raise ProviderError(f"Alpaca returned HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise ProviderError("Alpaca rate limit exceeded")
        if resp.status_code >= 400:
            raise ProviderError(f"Alpaca returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise ProviderError("Alpaca returned an unreadable response") from None

    def list_symbols(self, base_url: Optional[str] = None) -> list[str]:
        base = base_url or ASSETS_BASE_URL
        hit = _ASSETS_CACHE.get(base)
        if hit and time.time() - hit[0] < ASSETS_TTL_SECONDS:
            return list(hit[1])
        data = self._get("/v2/assets", {"status": "active", "asset_class": "us_equity"}, base=base)
        if not isinstance(data, list):
            raise ProviderError("Alpaca returned an unexpected assets response")
        symbols = filter_assets(data)
        _ASSETS_CACHE[base] = (time.time(), symbols)
        return list(symbols)

    def snapshots(self, symbols: list[str], feed: str = "iex") -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), SNAPSHOT_CHUNK):
            chunk = symbols[i : i + SNAPSHOT_CHUNK]
            data = self._get("/v2/stocks/snapshots", {"symbols": ",".join(chunk), "feed": feed})
            snaps = data.get("snapshots", data) if isinstance(data, dict) else {}
            for sym, snap in (snaps or {}).items():
                trade = (snap or {}).get("latestTrade") or {}
                t = trade.get("t")
                try:
                    ts = pd.Timestamp(t).to_pydatetime() if t else None
                except (ValueError, TypeError):
                    ts = None
                p = trade.get("p")
                try:
                    price = float(p) if p is not None else None
                except (ValueError, TypeError):
                    price = None
                out[sym] = {
                    "last_price": price,
                    "last_trade_time": ts,
                    "dailyBar": (snap or {}).get("dailyBar"),
                    "prevDailyBar": (snap or {}).get("prevDailyBar"),
                }
        return out

    def bars(self, symbols, start, end, timeframe="5Min", feed="sip", adjustment="raw") -> dict:
        frames: dict[str, list[dict]] = {}
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i : i + CHUNK]
            params = {
                "symbols": ",".join(chunk),
                "timeframe": timeframe,
                "start": _rfc3339(start),
                "end": _rfc3339(end),
                "feed": feed,
                "adjustment": adjustment,
                "limit": 10000,
            }
            while True:
                data = self._get("/v2/stocks/bars", params)
                for sym, bars in ((data or {}).get("bars") or {}).items():
                    frames.setdefault(sym, []).extend(bars or [])
                token = (data or {}).get("next_page_token")
                if not token:
                    break
                params = {**params, "page_token": token}
        out = {}
        for sym, rows in frames.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            idx = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
            df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            df.index = pd.DatetimeIndex(idx)
            out[sym] = df.sort_index()
        return out
