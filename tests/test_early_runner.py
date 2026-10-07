from datetime import datetime

import numpy as np
import pandas as pd

from core.providers.fake import FakeProvider
from core.scanner_base import ET, RunContext
from scanners import early_runner
from scanners.early_runner import EarlyRunnerScanner, evaluate_bars, size_check, stage1_candidates, trend_passes

NOW = datetime(2026, 10, 7, 16, 30, tzinfo=ET)
CFG = {**EarlyRunnerScanner.default_config}
N = 100


def make_df(last_close=6.5, last_vol=80000, base_vol=10000, price=5.0, n=N):
    """n-1 quiet sessions around `price`, then a final session at `last_close` on `last_vol`."""
    idx = pd.date_range(end="2026-10-07", periods=n, freq="B", tz=ET)
    close = np.full(n, price)
    close[-1] = last_close
    vol = np.full(n, float(base_vol))
    vol[-1] = last_vol
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": vol}, index=idx)


def bar(c, v=1000, t="2026-10-07T04:00:00Z"):
    return {"c": c, "v": v, "t": t}


def snap(close, prev):
    return {"dailyBar": bar(close, t="2026-10-07T04:00:00Z"), "prevDailyBar": bar(prev, t="2026-10-06T04:00:00Z")}


# -- stage 1 ---------------------------------------------------------------------

def test_stage1_filters_and_ranks():
    snaps = {
        "LOWP": snap(0.9, 0.5),
        "SMALL": snap(10.2, 10.0),  # +2% < 3%
        "EDGE": snap(10.3, 10.0),   # +3%
        "BIG": snap(15.0, 10.0),
        "ODD": {"dailyBar": "x"},
        "NONE": None,
    }
    out = stage1_candidates(snaps, CFG)
    assert [c["ticker"] for c in out] == ["BIG", "EDGE"]
    assert out[0]["bar_date"] == "2026-10-07"


# -- trend / volume -------------------------------------------------------------

def test_breakout_on_heavy_volume_passes():
    m = evaluate_bars("X", make_df(), CFG)
    assert m["rel_volume"] == 8.0
    assert m["close"] > m["prior_high"] and m["close"] > m["ema_fast"] > m["ema_slow"]
    assert trend_passes(m, CFG)


def test_each_condition_is_required():
    assert not trend_passes(evaluate_bars("X", make_df(last_vol=40000), CFG), CFG)  # 4x < 5x
    assert not trend_passes(evaluate_bars("X", make_df(last_close=5.02), CFG), CFG)  # no breakout
    assert not trend_passes(evaluate_bars("X", make_df(last_vol=9000, base_vol=1000), CFG), {**CFG, "min_volume": 10000})  # raw volume too low


def test_too_little_history_is_skipped():
    assert evaluate_bars("X", make_df(n=30), CFG) is None
    assert evaluate_bars("X", None, CFG) is None


# -- size ------------------------------------------------------------------------

def test_size_check():
    assert size_check(100e6, 10e6, CFG) is True
    assert size_check(600e6, 10e6, CFG) is False
    assert size_check(100e6, 30e6, CFG) is False
    assert size_check(None, 10e6, CFG) is None
    assert size_check(600e6, None, CFG) is False  # one known failure is enough
    assert size_check(None, None, CFG) is None


# -- whole run -------------------------------------------------------------------

def run_scan(monkeypatch, fundamentals, snapshots, daily, universe, **cfg):
    scanner = EarlyRunnerScanner()
    monkeypatch.setattr(scanner, "_fundamentals", lambda t: fundamentals[t] if not isinstance(fundamentals[t], Exception) else (_ for _ in ()).throw(fundamentals[t]))
    prov = FakeProvider(snapshots, {}, daily=daily, universe=universe)
    out = scanner.run([], {**CFG, **cfg}, RunContext(intraday_provider=prov, now=NOW))
    return out, prov


SMALL = {"market_cap": 120e6, "float_shares": 12e6, "short_pct_float": 18.5}


def test_full_run_flags_only_small_cap_low_float(monkeypatch):
    snapshots = {t: snap(6.5, 5.0) for t in ("WIN", "BIGCAP", "NODATA", "BOOM", "FLAT")}
    daily = {
        "WIN": make_df(), "BIGCAP": make_df(), "NODATA": make_df(), "BOOM": make_df(), "FLAT": make_df(last_vol=12000),
    }
    fundamentals = {
        "WIN": SMALL,
        "BIGCAP": {"market_cap": 9e9, "float_shares": 800e6, "short_pct_float": 1.0},
        "NODATA": {"market_cap": None, "float_shares": None, "short_pct_float": None},
        "BOOM": RuntimeError("yahoo down"),
        "FLAT": SMALL,
    }
    out, prov = run_scan(monkeypatch, fundamentals, snapshots, daily, list(snapshots))
    assert out.status == "ok"
    by = {r["ticker"]: r for r in out.rows}
    assert set(by) == {"WIN", "BIGCAP", "NODATA", "BOOM"}  # FLAT failed the volume test, so it is not listed
    assert [s.ticker for s in out.signals] == ["WIN"]
    assert by["WIN"]["flagged"] and by["WIN"]["size"] == "small enough" and by["WIN"]["market_cap_m"] == 120.0
    assert by["BIGCAP"]["size"] == "too big" and not by["BIGCAP"]["flagged"]
    assert by["NODATA"]["size"] == "unknown" and not by["NODATA"]["flagged"]
    assert by["BOOM"]["error"] == "size data unavailable" and not by["BOOM"]["flagged"]
    assert out.extra["swept"] == 5 and "1 flagged" in out.message
    sig = out.signals[0]
    assert sig.key == "WIN:early_runner" and sig.episode_id == "2026-10-07" and sig.episode_start < sig.episode_id


def test_stale_bars_are_not_scored(monkeypatch):
    snapshots = {"OLD": snap(6.5, 5.0)}  # snapshot says 10/07
    stale = make_df().iloc[:-1]  # bars end 10/06
    out, _ = run_scan(monkeypatch, {"OLD": SMALL}, snapshots, {"OLD": stale}, ["OLD"])
    assert out.rows == [] and out.signals == []


def test_fundamentals_limit_only_checks_strongest(monkeypatch):
    snapshots = {"A": snap(6.5, 5.0), "B": snap(6.5, 5.0)}
    daily = {"A": make_df(last_vol=100000), "B": make_df(last_vol=60000)}
    out, _ = run_scan(monkeypatch, {"A": SMALL, "B": SMALL}, snapshots, daily, ["A", "B"], fundamentals_check_max=1)
    by = {r["ticker"]: r for r in out.rows}
    assert by["A"]["flagged"] and by["B"]["size"] == "not checked" and not by["B"]["flagged"]


def test_not_configured_and_empty_market(monkeypatch):
    prov = FakeProvider({}, {}, configured=False)
    out = EarlyRunnerScanner().run([], CFG, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "not_configured"
    out, _ = run_scan(monkeypatch, {}, {"Q": snap(5.0, 5.0)}, {}, ["Q"])
    assert out.status == "ok" and out.rows == [] and "0 flagged" in out.message


def test_alert_message_has_size_and_chart_link(monkeypatch):
    snapshots = {"WIN": snap(6.5, 5.0)}
    out, _ = run_scan(monkeypatch, {"WIN": SMALL}, snapshots, {"WIN": make_df()}, ["WIN"])
    scanner = EarlyRunnerScanner()
    (payload, sigs), = scanner.build_alert_messages({"WIN": out.signals}, {"WIN": out.rows[0]})
    text = payload["embeds"][0]["fields"][0]["value"]
    assert "RelVol 8.0x" in text and "Cap $120M" in text and "Float 12.0M" in text and "Short 18.5%" in text and "tradingview.com/symbols/WIN" in text
    assert len(sigs) == 1
    assert early_runner.EarlyRunnerScanner().format_alert("WIN", out.signals, None)["embeds"]
