"""Intraday market-data provider interface (Alpaca today; others can be added)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional


class ProviderError(Exception):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderPermissionError(ProviderError):
    """The account's plan doesn't allow the requested feed."""


class IntradayProvider:
    name = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def snapshots(self, symbols: list[str], feed: str = "iex") -> dict[str, dict]:
        """{sym: {"last_price": float|None, "last_trade_time": datetime|None}}"""
        raise NotImplementedError

    def bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "5Min",
        feed: str = "sip",
        adjustment: str = "raw",
    ) -> dict:
        """{sym: DataFrame indexed by tz-aware America/New_York bar-start time,
        columns open, high, low, close, volume}"""
        raise NotImplementedError
