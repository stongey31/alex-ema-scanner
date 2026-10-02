# FROZEN copy of pre-refactor code for equivalence tests. Do not edit.
"""
alerts.py -- Discord webhook dispatcher.

Reads DISCORD_WEBHOOK_URL from the environment (a local .env file is
supported via python-dotenv for local testing). If the webhook URL is
missing or empty, the alert is logged to the console instead of raising --
a missing/misconfigured webhook must never crash a scan or the scheduled
runner.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

from tests.legacy.screener_v1 import signal_label

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("alerts")

COLOR_GREEN = 0x2ECC71


def _days_since(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except ValueError:
        return None


def _fmt_date(date_str: Optional[str], days: Optional[int], past: bool) -> str:
    if not date_str:
        return "N/A"
    if days is None:
        return date_str
    if past:
        return f"{date_str} ({days}d ago)"
    return f"{date_str} (in {-days}d)"


def _join_labels(labels: list[str]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def build_embed(setup_data: dict, signal_keys: list[str]) -> dict:
    ticker = setup_data.get("ticker", "?")
    price = setup_data.get("current_close")
    rsi = setup_data.get("rsi")
    rsi_min = setup_data.get("rsi_min_window")
    signals = setup_data.get("signals", {})
    last_earnings_date = setup_data.get("last_earnings_date")
    next_earnings_date = setup_data.get("next_earnings_date")

    labels = [signal_label(k) for k in signal_keys]
    tradingview_link = f"https://www.tradingview.com/symbols/{ticker}"

    fields = [
        {"name": "Current Price", "value": f"${price:,.2f}" if price is not None else "N/A", "inline": True},
        {"name": "RSI (14) Now", "value": f"{rsi:.1f}" if rsi is not None else "N/A", "inline": True},
        {
            "name": "Lowest RSI in Pullback",
            "value": f"{rsi_min:.1f}" if rsi_min is not None else "N/A",
            "inline": True,
        },
    ]
    for key in signal_keys:
        sig = signals.get(key, {})
        value = sig.get("value")
        dist = sig.get("dist_pct")
        touch_date = sig.get("touch_date")
        lines = [
            f"Line: ${value:,.2f}" if value is not None else "Line: N/A",
            f"Price vs line: {dist:+.2f}%" if dist is not None else "Price vs line: N/A",
            f"Touched: {touch_date}" if touch_date else "Touched: N/A",
        ]
        fields.append({"name": f"Bounce off {signal_label(key)}", "value": "\n".join(lines), "inline": True})

    fields += [
        {
            "name": "Last Earnings",
            "value": _fmt_date(last_earnings_date, _days_since(last_earnings_date), past=True),
            "inline": True,
        },
        {
            "name": "Next Earnings",
            "value": _fmt_date(next_earnings_date, _days_since(next_earnings_date), past=False),
            "inline": True,
        },
        {"name": "Chart", "value": f"[View on TradingView]({tradingview_link})", "inline": False},
    ]

    description = (
        f"Price pulled back to the {_join_labels(labels)} with RSI dipping into oversold territory, "
        "then reversed upward."
    )
    if "ema200" in signal_keys:
        description += " The 200 EMA bounce also came shortly after an earnings report."

    return {
        "embeds": [
            {
                "title": f"{ticker}: Bounce off {_join_labels(labels)}",
                "description": description,
                "color": COLOR_GREEN,
                "fields": fields,
                "url": tradingview_link,
            }
        ]
    }


def send_alert(setup_data: dict, signal_keys: list[str]) -> bool:
    """Send one Discord embed alert for a ticker's newly triggered signals.

    Returns True if a webhook post was attempted and succeeded, False
    otherwise (including the "no webhook configured" case, which is not
    treated as an error -- it just logs to console instead).
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    payload = build_embed(setup_data, signal_keys)

    if not webhook_url:
        log.info(
            "DISCORD_WEBHOOK_URL not set -- logging alert to console instead:\n%s",
            payload,
        )
        return False

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        log.info("Discord alert sent for %s (%s)", setup_data.get("ticker"), ", ".join(signal_keys))
        return True
    except requests.RequestException as e:
        log.error("Failed to send Discord alert for %s: %s", setup_data.get("ticker"), e)
        return False


if __name__ == "__main__":
    demo = {
        "ticker": "AAPL",
        "current_close": 230.12,
        "rsi": 41.3,
        "rsi_min_window": 28.4,
        "last_earnings_date": "2026-09-12",
        "next_earnings_date": "2026-12-11",
        "signals": {
            "ema200": {"value": 225.50, "dist_pct": 2.05, "touch_date": "2026-09-15"},
            "sma50": {"value": 226.10, "dist_pct": 1.78, "touch_date": "2026-09-16"},
        },
    }
    send_alert(demo, ["ema200", "sma50"])
