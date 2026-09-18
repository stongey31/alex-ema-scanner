"""
runner.py -- headless entry point for the scheduled scan.

This is what the GitHub Actions workflow (.github/workflows/daily-scan.yml)
runs once a day. It reads the REAL, persistent watchlist from
data/watchlist.json (not anything typed into the Streamlit dashboard --
that's session-only and separate), scans it, and fires a Discord alert for
any ticker that currently has BOTH is_bounce and earnings_recent true.

Deduplication: data/watchlist.json's "alerted" map records, per ticker, the
last earnings date we already alerted on (as a "YYYY-MM-DD" string). If a
ticker still matches tomorrow because the same earnings event is still
within the lookback window, we do NOT re-alert for that same earnings date.
A new alert only fires once the ticker's last_earnings_date changes (i.e. a
new earnings report) or a previously-unseen ticker starts matching.
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


def run() -> list[dict]:
    config = load_watchlist()
    tickers = config.get("tickers", [])
    proximity_pct = config.get("proximity_threshold_pct", 2.0)
    earnings_lookback_days = config.get("earnings_lookback_days", 7)
    bounce_lookback_days = config.get("bounce_lookback_days", 10)
    alerted = config.setdefault("alerted", {})

    log.info("Scanning %d tickers: %s", len(tickers), ", ".join(tickers))
    results = scan_watchlist(
        tickers,
        proximity_pct=proximity_pct,
        earnings_lookback_days=earnings_lookback_days,
        bounce_lookback_days=bounce_lookback_days,
    )

    matches = [r for r in results if r.get("is_bounce") and r.get("earnings_recent")]
    log.info("%d matching setup(s) found", len(matches))

    sent_any = False
    for match in matches:
        ticker = match["ticker"]
        last_earnings_date = match.get("last_earnings_date")

        if not last_earnings_date:
            log.warning("Skipping alert for %s: matched but has no last_earnings_date to dedupe on", ticker)
            continue

        if alerted.get(ticker) == last_earnings_date:
            log.info(
                "Skipping %s: already alerted for earnings date %s",
                ticker,
                last_earnings_date,
            )
            continue

        send_alert(match)
        alerted[ticker] = last_earnings_date
        sent_any = True

    if sent_any:
        save_watchlist(config)
        log.info("Updated %s with new alert log entries", WATCHLIST_PATH)
    else:
        log.info("No new alerts fired; watchlist file left unchanged")

    return matches


if __name__ == "__main__":
    run()
