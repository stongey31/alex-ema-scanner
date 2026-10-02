"""Read secrets from the environment (.env supported) or Streamlit secrets. Never logs values."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def get_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def has_secret(name: str) -> bool:
    return bool(get_secret(name))
