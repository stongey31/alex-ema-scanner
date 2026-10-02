"""Golden tests: the refactored bounce scanner behaves exactly like the frozen legacy code."""

import pandas as pd
import pytest
import yfinance

from core.scanner_base import RunContext
from scanners import ma_bounce
from scanners.ma_bounce import MaBounceScanner
from tests.bounce_fixtures import FakeTicker, earnings_frame, ohlcv, pullback_series, random_walk
from tests.legacy import alerts_v1
from tests.legacy import screener_v1 as legacy

PULLBACK_50 = dict(trend_pct=0.002, down_days=4, down_pct=0.015, flat_days=0, up_days=2, up_pct=0.015)
PULLBACK_200 = dict(trend_pct=0.001, down_days=6, down_pct=0.015, flat_days=0, up_days=2, up_pct=0.01)
BOUNCE_NO_RSI = dict(trend_pct=0.002, down_days=4, down_pct=0.008, flat_days=0, up_days=2, up_pct=0.01)

RECENT = earnings_frame([3, 90], [60])
STALE = earnings_frame([30, 120], [60])


def _case(history, earnings=None):
    return {"history": history, "earnings": earnings}


@pytest.fixture
def universe(monkeypatch):
    cases = {}
    for i in range(32):
        cases[f"RW{i:02d}"] = _case(random_walk(i), earnings_frame([40 + i], [50]))
    cases["CASE_A"] = _case(pullback_series(**PULLBACK_50), STALE)  # 50 SMA/EMA bounce, RSI<30
    cases["CASE_B"] = _case(pullback_series(**PULLBACK_200), RECENT)  # 200 EMA, earnings 3 days ago
    cases["CASE_C"] = _case(pullback_series(**PULLBACK_200), STALE)  # same, earnings 30 days ago
    cases["CASE_D"] = _case(pullback_series(**BOUNCE_NO_RSI), STALE)  # bounce but no RSI<30
    cases["CASE_E"] = _case(pd.DataFrame())  # empty history
    cases["CASE_F"] = _case(random_walk(99).iloc[:150])  # < 200 rows
    cases["CASE_G"] = _case(RuntimeError("boom"))  # history() raises
    cases["CASE_H"] = _case(pullback_series(**PULLBACK_200), None)  # earnings None
    cases["CASE_I_NAIVE"] = _case(pullback_series(**PULLBACK_200), earnings_frame([3], [60], tz=None))
    cases["CASE_I_AWARE"] = _case(pullback_series(**PULLBACK_200), earnings_frame([3], [60], tz="UTC"))
    cases["CASE_J"] = _case(pullback_series(**PULLBACK_200), RuntimeError("no calendar"))
    cases["CASE_K"] = _case(pullback_series(**PULLBACK_200), pd.DataFrame())
    FakeTicker.registry = cases
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    return list(cases)


def test_scan_watchlist_deep_equal(universe):
    old = legacy.scan_watchlist(universe)
    new = ma_bounce.scan_watchlist(universe)
    assert old == new


def test_fixture_exercises_the_interesting_cases(universe):
    res = {r["ticker"]: r for r in ma_bounce.scan_watchlist(universe)}
    triggered = [t for t, r in res.items() if r["signals_triggered"]]
    assert triggered, "at least one ticker must trigger"
    near_miss = [
        t for t, r in res.items()
        if any(s["is_bounce"] for s in r["signals"].values()) and not r["signals_triggered"]
    ]
    assert near_miss, "need a bounce that did not trigger"
    assert "ema200" in res["CASE_B"]["signals_triggered"]
    assert res["CASE_C"]["signals"]["ema200"]["is_bounce"]
    assert "ema200" not in res["CASE_C"]["signals_triggered"]  # earnings gate
    assert "ema50" in res["CASE_A"]["signals_triggered"]
    d = res["CASE_D"]
    assert any(s["is_bounce"] for s in d["signals"].values()) and not d["rsi_oversold"]
    assert res["CASE_E"]["error"] and res["CASE_F"]["error"] and res["CASE_G"]["error"]
    assert res["CASE_H"]["last_earnings_date"] is None
    assert "ema200" in res["CASE_I_NAIVE"]["signals_triggered"]
    assert "ema200" in res["CASE_I_AWARE"]["signals_triggered"]


def test_plugin_rows_and_signals_match_legacy(universe):
    old = legacy.scan_watchlist(universe)
    out = MaBounceScanner().run(universe, MaBounceScanner.default_config, RunContext())
    assert out.status == "ok"
    assert [{k: v for k, v in r.items() if k != "flagged"} for r in out.rows] == old
    assert [r["flagged"] for r in out.rows] == [bool(r["signals_triggered"]) for r in old]
    expected = [
        (r["ticker"], f'{r["ticker"]}:{k}', r["signals"][k]["touch_date"], r.get("window_start"))
        for r in old
        for k in r["signals_triggered"]
    ]
    assert [(s.ticker, s.key, s.episode_id, s.episode_start) for s in out.signals] == expected
    assert expected


def test_embed_matches_legacy(universe):
    scanner = MaBounceScanner()
    out = scanner.run(universe, scanner.default_config, RunContext())
    checked = 0
    for row in out.rows:
        keys = row["signals_triggered"]
        if not keys:
            continue
        sigs = [s for s in out.signals if s.ticker == row["ticker"]]
        assert scanner.format_alert(row["ticker"], sigs, row) == alerts_v1.build_embed(row, keys)
        checked += 1
    assert checked >= 3


def test_table_columns_match_old_dashboard(universe):
    scanner = MaBounceScanner()
    out = scanner.run(["CASE_A"], scanner.default_config, RunContext())
    cols = list(scanner.table(out).columns)
    assert cols[:4] == ["Ticker", "Price", "RSI", "Low RSI (window)"]
    assert cols[-4:] == ["Signals", "Last Earnings", "Next Earnings", "Error"]
    assert "vs 200 EMA" in cols and "50 SMA Bounce?" in cols
