"""
runner.py -- headless entry point for the scheduled scans.

GitHub Actions runs this (see .github/workflows/): `--lane daily` once a day
after the close, `--lane intraday` in the pre-market window. It reads the REAL,
persistent settings from data/ (not anything typed into the Streamlit
dashboard -- that's session-only), runs every enabled scanner in the lane, and
sends Discord alerts for NEW signals only.

Deduplication: data/alert_log.json maps scanner id -> "TICKER:signal" -> the
episode id (e.g. the touch date) we already alerted on. A signal is skipped
while the stored episode is still >= the signal's episode_start (same episode).
An alert is recorded only after Discord accepted it, so a missing webhook or a
failed post never silently swallows an alert.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from core import dedup
from core.config import ConfigError, get_universe, load_scanner_config
from core.notify import post_discord
from core.registry import BrokenScanner, discover_scanners
from core.scanner_base import ET, RunContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("runner")


def _in_window(now: datetime, window: str) -> bool:
    start_s, end_s = window.split("-")
    fmt = "%H:%M"
    start = datetime.strptime(start_s.strip(), fmt).time()
    end = datetime.strptime(end_s.strip(), fmt).time()
    return start <= now.time().replace(second=0, microsecond=0) <= end


def run_scanner(scanner, alert_log: dict, dry_run: bool) -> bool:
    """Run one scanner. Returns True if the alert log changed."""
    cfg = load_scanner_config(scanner)
    if not cfg.get("enabled", True):
        log.info("[%s] disabled in config; skipping", scanner.id)
        return False
    tickers = get_universe(cfg, None)

    log.info("[%s] Scanning %d tickers: %s", scanner.id, len(tickers), ", ".join(tickers))
    output = scanner.run(tickers, cfg, RunContext())
    if output.status == "not_configured":
        log.info("[%s] not configured: %s", scanner.id, output.message)
        return False
    if output.status == "error":
        log.error("[%s] error: %s", scanner.id, output.message)
        return False
    if output.message:
        log.info("[%s] %s", scanner.id, output.message)

    scanner_log = alert_log.setdefault(scanner.id, {})
    log.info("[%s] %d ticker(s) with at least one signal", scanner.id, len({s.ticker for s in output.signals}))

    hits: dict[str, list] = {}
    for s in output.signals:
        if dedup.is_duplicate(scanner_log, s.key, s.episode_start):
            log.info("Skipping %s: already alerted for touch on %s", s.key, scanner_log[s.key])
            continue
        hits.setdefault(s.ticker, []).append(s)
    if not hits:
        return False

    rows_by_ticker: dict[str, dict] = {}
    for r in output.rows:
        rows_by_ticker.setdefault(r.get("ticker"), r)

    changed = False
    for payload, sigs in scanner.build_alert_messages(hits, rows_by_ticker):
        if dry_run:
            log.info("[%s] dry run: would send alert for %s", scanner.id, ", ".join(s.key for s in sigs))
            continue
        # Only log it as alerted once Discord actually accepted it.
        if post_discord(payload, context=f"{scanner.id}: " + ", ".join(s.key for s in sigs)):
            for s in sigs:
                scanner_log[s.key] = s.episode_id
            changed = True
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run scheduled scans and send Discord alerts.")
    ap.add_argument("--lane", choices=["daily", "intraday"], default="daily")
    ap.add_argument("--only", help="run only this scanner id")
    ap.add_argument("--list", action="store_true", help="list discovered scanners and exit")
    ap.add_argument("--dry-run", action="store_true", help="no Discord posts, no alert-log writes")
    ap.add_argument("--et-window", help="HH:MM-HH:MM; exit quietly if the current New York time is outside it")
    args = ap.parse_args(argv)

    found = discover_scanners()
    if args.list:
        for sid, obj in found:
            if isinstance(obj, BrokenScanner):
                print(f"{sid}\tBROKEN\t{obj.error}")
            else:
                print(f"{sid}\t{obj.lane}\t{obj.name}")
        return 0

    if args.et_window:
        now = datetime.now(ET)
        if not _in_window(now, args.et_window):
            log.info("Outside ET window %s (now %s ET); nothing to do", args.et_window, now.strftime("%H:%M"))
            return 0

    alert_log = dedup.load_alert_log()
    changed = False
    for sid, obj in found:
        if isinstance(obj, BrokenScanner):
            log.error("[%s] could not be loaded: %s", sid, obj.error)
            continue
        if obj.lane != args.lane or (args.only and sid != args.only):
            continue
        try:
            changed |= run_scanner(obj, alert_log, args.dry_run)
        except ConfigError as e:
            log.error("[%s] config problem: %s", sid, e)
        except Exception:
            log.exception("[%s] scanner crashed; continuing with the others", sid)

    if changed and not args.dry_run:
        dedup.save_alert_log(alert_log)
        log.info("Updated %s with new alert log entries", dedup.ALERT_LOG_PATH)
    else:
        log.info("No new alerts recorded; alert log left unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
