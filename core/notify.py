"""Discord webhook posting. A missing/broken webhook never crashes a scan."""

from __future__ import annotations

import logging

import requests

from core.secrets import get_secret

log = logging.getLogger("notify")


def post_discord(payload: dict, context: str = "") -> bool:
    """Post one payload. True only if Discord accepted it (no webhook -> logs and returns False)."""
    webhook_url = get_secret("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        log.info("DISCORD_WEBHOOK_URL not set -- logging alert to console instead:\n%s", payload)
        return False
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        log.info("Discord alert sent%s", f" for {context}" if context else "")
        return True
    except requests.RequestException as e:
        # str(e) can contain the URL for some errors; log only the exception type + status.
        status = getattr(getattr(e, "response", None), "status_code", None)
        log.error("Failed to send Discord alert%s: %s%s", f" for {context}" if context else "",
                  type(e).__name__, f" (HTTP {status})" if status else "")
        return False
