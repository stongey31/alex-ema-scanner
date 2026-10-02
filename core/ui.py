"""Streamlit helpers shared by scanner tabs."""

from __future__ import annotations

import streamlit as st

_SKIP = {"enabled", "watchlist", "assets_base_url", "exclude_etfs"}
UNIVERSE_MODES = ["whole_market", "watchlist"]


def _cast(v, typ):
    # Streamlit requires value/min/max/step to share one numeric type.
    return None if v is None else typ(v)


def settings_form(scanner, config: dict, key_prefix: str) -> dict:
    """One widget per setting. Returns the session-only edited config."""
    edited = dict(config)
    meta = scanner.setting_meta
    for key, value in config.items():
        if key in _SKIP:
            continue
        m = meta.get(key, {})
        label = m.get("label", key)
        help_ = m.get("help")
        wkey = f"{key_prefix}_{key}"
        if key == "universe_mode":
            edited[key] = st.selectbox(
                "Universe", UNIVERSE_MODES, index=UNIVERSE_MODES.index(value) if value in UNIVERSE_MODES else 0,
                format_func=lambda v: "Whole market (all US stocks)" if v == "whole_market" else "Watchlist (fixed tickers)",
                help="Whole market sweeps every tradable US stock for gappers first (~26 API calls per run).", key=wkey,
            )
            continue
        if isinstance(value, bool):
            edited[key] = st.checkbox(label, value=value, help=help_, key=wkey)
        elif isinstance(value, int):
            edited[key] = int(
                st.number_input(
                    label, value=value, step=int(m.get("step", 1)),
                    min_value=_cast(m.get("min"), int), max_value=_cast(m.get("max"), int), help=help_, key=wkey,
                )
            )
        elif isinstance(value, float):
            edited[key] = float(
                st.number_input(
                    label, value=value, step=float(m.get("step", 0.1)),
                    min_value=_cast(m.get("min"), float), max_value=_cast(m.get("max"), float), help=help_, key=wkey,
                )
            )
        elif isinstance(value, list):
            raw = st.text_input(label, value=", ".join(str(v) for v in value), help=help_, key=wkey)
            edited[key] = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(value, str):
            edited[key] = st.text_input(label, value=value, help=help_, key=wkey)
    return edited
