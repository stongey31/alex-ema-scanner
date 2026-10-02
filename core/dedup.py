"""Alert log: remembers which signals were already sent so they don't repeat."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.config import DATA_DIR

ALERT_LOG_PATH = DATA_DIR / "alert_log.json"


def load_alert_log() -> dict:
    path = ALERT_LOG_PATH
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_alert_log(log: dict) -> None:
    with open(ALERT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
        f.write("\n")


def is_duplicate(log_for_scanner: dict, key: str, episode_start: Optional[str]) -> bool:
    """True if this key was already alerted for the current episode."""
    prior = log_for_scanner.get(key)
    return bool(prior and episode_start and prior >= episode_start)
