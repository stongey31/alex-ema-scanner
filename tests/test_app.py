from pathlib import Path

import pytest
import requests
import yfinance
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)

    def boom(*a, **k):
        raise AssertionError("network call during app boot")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(yfinance, "Ticker", boom)


def test_app_boots_without_network_or_keys():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert "Moving Average Bounce" in labels and "Pre-market Momentum" in labels
    assert any("Alpaca API keys are not set up" in i.value for i in at.info)
    assert not any(l.startswith("⚠") for l in labels)


def test_fake_demo_checkbox_runs_with_clear_warning():
    at = AppTest.from_file(APP, default_timeout=60).run()
    demo = next(c for c in at.checkbox if c.key == "demo_premarket_momentum")
    demo.check().run()
    next(b for b in at.button if b.key == "run_premarket_momentum").click().run()
    assert not at.exception
    assert any("FAKE DATA" in w.value for w in at.warning)


def test_whole_market_tab_has_no_tickers_box_and_demo_runs_it():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert not any(t.key == "tickers_premarket_momentum" for t in at.text_input)
    assert any("Scanning whole market" in c.value for c in at.caption)
    sel = next(s for s in at.selectbox if s.key == "premarket_momentum_universe_mode")
    assert sel.value == "whole_market"
    assert next(t for t in at.toggle if t.key == "auto_premarket_momentum").value is False
    next(c for c in at.checkbox if c.key == "demo_premarket_momentum").check().run()
    next(b for b in at.button if b.key == "run_premarket_momentum").click().run()
    assert not at.exception
    assert any("FAKE DATA" in w.value for w in at.warning)
    assert any("Swept 300 symbols" in i.value for i in at.info)


def test_watchlist_mode_shows_tickers_box():
    at = AppTest.from_file(APP, default_timeout=60).run()
    sel = next(s for s in at.selectbox if s.key == "premarket_momentum_universe_mode")
    sel.set_value("watchlist").run()
    assert not at.exception
    assert any(t.key == "tickers_premarket_momentum" for t in at.text_input)
