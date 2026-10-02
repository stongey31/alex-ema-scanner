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
CHUNK = 100
MAX_RETRIES = 3


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

    def _get(self, path: str, params: dict) -> dict:
        headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret}
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
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

    def snapshots(self, symbols: list[str], feed: str = "iex") -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i : i + CHUNK]
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
                out[sym] = {"last_price": float(p) if p is not None else None, "last_trade_time": ts}
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
