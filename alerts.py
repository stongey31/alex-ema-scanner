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

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("alerts")

COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C


def _days_since(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except ValueError:
        return None


def build_embed(setup_data: dict) -> dict:
    ticker = setup_data.get("ticker", "?")
    price = setup_data.get("current_close")
    ema = setup_data.get("ema200")
    dist_pct = setup_data.get("dist_pct")
    last_earnings_date = setup_data.get("last_earnings_date")
    days_since_earnings = _days_since(last_earnings_date)

    above_ema = dist_pct is not None and dist_pct >= 0
    color = COLOR_GREEN if above_ema else COLOR_RED

    tradingview_link = f"https://www.tradingview.com/symbols/{ticker}"

    fields = [
        {"name": "Current Price", "value": f"${price:,.2f}" if price is not None else "N/A", "inline": True},
        {"name": "200-Day EMA", "value": f"${ema:,.2f}" if ema is not None else "N/A", "inline": True},
        {
            "name": "% Distance from EMA",
            "value": f"{dist_pct:+.2f}%" if dist_pct is not None else "N/A",
            "inline": True,
        },
        {
            "name": "Days Since Earnings",
            "value": str(days_since_earnings) if days_since_earnings is not None else "N/A",
            "inline": True,
        },
        {"name": "Chart", "value": f"[View on TradingView]({tradingview_link})", "inline": False},
    ]

    return {
        "embeds": [
            {
                "title": f"{ticker}: Post-Earnings 200 EMA Bounce",
                "description": "Price recently pulled back to the 200-day EMA and reversed upward, shortly after an earnings report.",
                "color": color,
                "fields": fields,
                "url": tradingview_link,
            }
        ]
    }


def send_alert(setup_data: dict) -> bool:
    """Send a Discord embed alert for one matching setup.

    Returns True if a webhook post was attempted and succeeded, False
    otherwise (including the "no webhook configured" case, which is not
    treated as an error -- it just logs to console instead).
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    payload = build_embed(setup_data)

    if not webhook_url:
        log.info(
            "DISCORD_WEBHOOK_URL not set -- logging alert to console instead:\n%s",
            payload,
        )
        return False

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        log.info("Discord alert sent for %s", setup_data.get("ticker"))
        return True
    except requests.RequestException as e:
        log.error("Failed to send Discord alert for %s: %s", setup_data.get("ticker"), e)
        return False


if __name__ == "__main__":
    demo = {
        "ticker": "AAPL",
        "current_close": 230.12,
        "ema200": 225.50,
        "dist_pct": 2.05,
        "is_bounce": True,
        "earnings_recent": True,
        "last_earnings_date": "2026-09-12",
    }
    send_alert(demo)
