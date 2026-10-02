"""Plugin contract: every scanner in scanners/ subclasses Scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")


@dataclass
class Signal:
    ticker: str
    key: str  # dedup key, e.g. "AAPL:sma50"
    episode_id: str  # identifies one occurrence (e.g. the touch date)
    episode_start: Optional[str] = None  # an earlier-or-equal episode_id counts as the same episode
    data: dict = field(default_factory=dict)


@dataclass
class ScanOutput:
    status: str = "ok"  # "ok" | "not_configured" | "error"
    message: str = ""
    rows: list[dict] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)


@dataclass
class RunContext:
    intraday_provider: Any = None
    now: Optional[datetime] = None

    def __post_init__(self):
        if self.now is None:
            self.now = datetime.now(ET)


class Scanner:
    id: str = ""  # set by the registry (= the module file name)
    name: str = "Unnamed scanner"
    description: str = ""
    lane: str = "daily"  # "daily" | "intraday"
    default_config: dict = {}
    setting_meta: dict = {}  # key -> {"label", "help", "min", "max", "step"}
    column_formats: dict = {}

    def run(self, tickers: list[str], config: dict, ctx: RunContext) -> ScanOutput:
        raise NotImplementedError

    def table(self, output: ScanOutput) -> pd.DataFrame:
        return pd.DataFrame(output.rows)

    def format_alert(self, ticker: str, signals: list[Signal], row: Optional[dict]) -> dict:
        fields = []
        for s in signals:
            for k, v in s.data.items():
                fields.append({"name": str(k), "value": str(v), "inline": True})
        return {"embeds": [{"title": f"{ticker}: {self.name}", "fields": fields[:25]}]}

    def build_alert_messages(
        self, hits: dict[str, list[Signal]], rows_by_ticker: dict[str, dict]
    ) -> list[tuple[dict, list[Signal]]]:
        return [(self.format_alert(t, sigs, rows_by_ticker.get(t)), sigs) for t, sigs in hits.items()]

    def render_detail(self, output: ScanOutput, config: dict) -> None:
        return None
