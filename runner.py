"""
runner.py -- headless entry point for the scheduled scan.

This is what the GitHub Actions workflow (.github/workflows/daily-scan.yml)
runs once a day. It reads the REAL, persistent watchlist from
data/watchlist.json (not anything typed into the Streamlit dashboard --
that's session-only and separate), scans it, and fires one Discord alert per
ticker that has at least one NEW triggered signal (see screener.py for the
four signals and their trigger rules).

Deduplication: data/watchlist.json's "alerted" map records, per
"TICKER:signal" key (e.g. "AAPL:sma50"), the touch date of the bounce we
already alerted on. While that touch date is still inside the current
bounce lookback window it's the same pullback, so we do NOT re-alert. Once it
has rolled out of the window, a fresh bounce on that same line alerts again.
The workflow commits this file back to the repo after each run so the log
survives between days.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from alerts import send_alert
from screener import scan_watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("runner")

WATCHLIST_PATH = Path(__file__).parent / "data" / "watchlist.json"


def load_watchlist(path: Path = WATCHLIST_PATH) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def save_watchlist(config: dict, path: Path = WATCHLIST_PATH) -> None:
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def _already_alerted(alerted: dict, key: str, window_start: str | None) -> bool:
    """True if this ticker+signal was already alerted for the current pullback."""
    previous_touch = alerted.get(key)
    if not previous_touch or not window_start:
        return False
    # Same pullback episode: the touch we alerted on is still inside today's window.
    return previous_touch >= window_start


def run() -> list[dict]:
    config = load_watchlist()
    tickers = config.get("tickers", [])
    proximity_pct = config.get("proximity_threshold_pct", 2.0)
    earnings_lookback_days = config.get("earnings_lookback_days", 7)
    bounce_lookback_days = config.get("bounce_lookback_days", 10)
    rsi_period = config.get("rsi_period", 14)
    rsi_threshold = config.get("rsi_oversold_threshold", 30)
    alerted = config.setdefault("alerted", {})

    log.info("Scanning %d tickers: %s", len(tickers), ", ".join(tickers))
    results = scan_watchlist(
        tickers,
        proximity_pct=proximity_pct,
        earnings_lookback_days=earnings_lookback_days,
        bounce_lookback_days=bounce_lookback_days,
        rsi_period=rsi_period,
        rsi_threshold=rsi_threshold,
    )

    matches = [r for r in results if r.get("signals_triggered")]
    log.info("%d ticker(s) with at least one triggered signal", len(matches))

    sent_any = False
    for match in matches:
        ticker = match["ticker"]
        new_signals = []
        for key in match["signals_triggered"]:
            alert_key = f"{ticker}:{key}"
            if _already_alerted(alerted, alert_key, match.get("window_start")):
                log.info("Skipping %s: already alerted for touch on %s", alert_key, alerted[alert_key])
                continue
            new_signals.append(key)

        if not new_signals:
            continue

        # Only log it as alerted once Discord actually accepted it -- otherwise
        # a missing webhook or a failed post would silently swallow the alert.
        if not send_alert(match, new_signals):
            continue
        for key in new_signals:
            alerted[f"{ticker}:{key}"] = match["signals"][key]["touch_date"]
        sent_any = True

    if sent_any:
        save_watchlist(config)
        log.info("Updated %s with new alert log entries", WATCHLIST_PATH)
    else:
        log.info("No new alerts fired; watchlist file left unchanged")

    return matches


if __name__ == "__main__":
    run()
