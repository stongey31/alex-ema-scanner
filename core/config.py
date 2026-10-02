"""Loads watchlists and per-scanner settings from data/ (edited by hand on GitHub)."""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class ConfigError(Exception):
    pass


def _read_json(path: Path, label: str):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{label} has a typo near line {e.lineno}: {e.msg}") from e


def load_watchlists() -> dict[str, list[str]]:
    path = DATA_DIR / "watchlists.json"
    if not path.exists():
        return {}
    return _read_json(path, "data/watchlists.json")


def load_scanner_config(scanner) -> dict:
    cfg = {"enabled": True, **scanner.default_config}
    path = DATA_DIR / "config" / f"{scanner.id}.json"
    if path.exists():
        cfg.update(_read_json(path, f"data/config/{scanner.id}.json"))
    return cfg


def clean_tickers(tickers) -> list[str]:
    out = []
    for t in tickers:
        t = str(t).strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def resolve_tickers(config: dict, watchlists: dict) -> list[str]:
    if "tickers" in config:
        return clean_tickers(config["tickers"])
    name = config.get("watchlist")
    if name not in watchlists:
        raise ConfigError(f'Unknown watchlist "{name}" -- check data/watchlists.json')
    return clean_tickers(watchlists[name])


def get_universe(cfg: dict, provider=None) -> list[str]:
    """Which tickers to scan. Today: the configured watchlist/tickers.

    Planned: a whole-market mode (like DAS Trader) can be added here without
    changing the scanner contract -- `provider` is passed for that reason.
    """
    return resolve_tickers(cfg, load_watchlists())
