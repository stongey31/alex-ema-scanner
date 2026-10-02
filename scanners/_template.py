"""TEMPLATE SCANNER -- "20-day breakout".

Copy this file to scanners/my_scanner_name.py; the file name becomes the
scanner's ID. Files starting with "_" (like this one) are ignored by the app,
so this template itself never shows up as a tab.

What this example does: flags tickers whose latest daily close is ABOVE the
highest high of the previous 20 trading days (a "breakout"). It needs no
settings file -- the defaults below are used unless you create
data/config/<your_file_name>.json.

Rules for a scanner file:
  * exactly ONE class that inherits from Scanner
  * a run() method that returns a ScanOutput
Everything else (the dashboard tab, the settings form, daily Discord alerts,
"don't alert twice") is handled for you automatically.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from core.scanner_base import RunContext, ScanOutput, Scanner, Signal


class BreakoutScanner(Scanner):
    # --- What people see on the dashboard ---------------------------------
    name = "20-Day Breakout"  # the tab title
    description = "Flags tickers whose latest close is above the highest high of the prior N days."

    # "daily" scanners run once a day after the close (see .github/workflows/daily-scan.yml).
    # "intraday" scanners run in the pre-market workflow and need a data provider.
    lane = "daily"

    # --- Settings -----------------------------------------------------------
    # "watchlist" picks a list from data/watchlists.json. Every other key shows
    # up as an editable box on the dashboard (this session only). To change a
    # value permanently, create data/config/<file_name>.json with just the keys
    # you want to override, e.g. {"lookback_days": 30}. Add "enabled": false to
    # turn the scanner off.
    default_config = {"watchlist": "mega_caps", "lookback_days": 20}

    # Optional: nicer labels and limits for the settings boxes.
    setting_meta = {
        "lookback_days": {"label": "Look-back days", "help": "How many prior days make up the breakout level", "min": 5, "max": 250, "step": 1},
    }

    # Optional: how table columns are displayed.
    column_formats = {"close": "${:.2f}", "prior_high": "${:.2f}", "above_pct": "{:+.2f}%"}

    # --- The actual logic -----------------------------------------------------
    def run(self, tickers, config, ctx: RunContext) -> ScanOutput:
        n = int(config.get("lookback_days", 20))
        rows, signals = [], []

        for ticker in tickers:
            # One bad ticker must never stop the whole scan: always catch errors per ticker.
            try:
                hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
                if hist is None or len(hist.dropna(subset=["Close"])) < n + 1:
                    rows.append({"ticker": ticker, "flagged": False, "error": "not enough price history"})
                    continue
                hist = hist.dropna(subset=["Close"])

                close = float(hist["Close"].iloc[-1])
                prior_high = float(hist["High"].iloc[-(n + 1) : -1].max())  # the N days BEFORE today
                above_pct = (close - prior_high) / prior_high * 100.0
                flagged = close > prior_high
                today = hist.index[-1].strftime("%Y-%m-%d")

                rows.append(
                    {
                        "ticker": ticker,
                        "close": round(close, 2),
                        "prior_high": round(prior_high, 2),
                        "above_pct": round(above_pct, 2),
                        "flagged": flagged,  # the dashboard highlights flagged rows
                        "error": None,
                    }
                )
                if flagged:
                    # A Signal is one thing worth an alert.
                    #   key        -> must be unique per ticker+idea ("TICKER:name")
                    #   episode_id -> identifies THIS occurrence (here: the date)
                    #   episode_start -> the oldest episode_id that still counts as the
                    #                    same occurrence. Using the same date for both
                    #                    means "alert once per day at most".
                    signals.append(
                        Signal(ticker, f"{ticker}:breakout", today, today, data={"close": close, "prior_high": prior_high})
                    )
            except Exception as e:
                rows.append({"ticker": ticker, "flagged": False, "error": str(e)})

        return ScanOutput("ok", "", rows, signals)

    # --- Discord message for one ticker (optional; a simple default exists) ----
    def format_alert(self, ticker, signals, row) -> dict:
        d = signals[0].data
        return {
            "embeds": [
                {
                    "title": f"{ticker}: broke out above its {self.default_config['lookback_days']}-day high",
                    "description": f"Closed at ${d['close']:,.2f}, above the prior high of ${d['prior_high']:,.2f}.",
                    "color": 0x3498DB,
                    "url": f"https://www.tradingview.com/symbols/{ticker}",
                }
            ]
        }
