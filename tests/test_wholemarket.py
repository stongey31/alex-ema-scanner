"""Whole-market universe mode: pick_prev_close, stage 1 filter, end-to-end, chunking (all offline)."""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import requests

from core.providers import alpaca as alpaca_mod
from core.providers.alpaca import AlpacaProvider, filter_assets
from core.providers.base import ProviderAuthError
from core.providers.fake import FakeProvider, make_demo_universe_provider, make_premarket_bars
from core.scanner_base import ET, RunContext
from scanners.premarket_momentum import PremarketMomentumScanner, get_universe, pick_prev_close, stage1_candidates

NOW = datetime(2026, 9, 30, 8, 30, tzinfo=ET)  # Wednesday
TODAY = NOW.date()
CFG = dict(PremarketMomentumScanner.default_config)
YDAY, DBY = "2026-09-29T04:00:00Z", "2026-09-28T04:00:00Z"


def bar(t, c):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": 1000}


# -- pick_prev_close -----------------------------------------------------------

def test_prev_close_after_hours_shape():
    snap = {"dailyBar": bar("2026-09-30T04:00:00Z", 11.0), "prevDailyBar": bar(YDAY, 10.0)}
    assert pick_prev_close(snap, TODAY) == 10.0


def test_prev_close_premarket_shape_a():  # dailyBar = yesterday, prevDailyBar = day before
    snap = {"dailyBar": bar(YDAY, 10.0), "prevDailyBar": bar(DBY, 9.0)}
    assert pick_prev_close(snap, TODAY) == 10.0


def test_prev_close_premarket_shape_b():  # no dailyBar
    assert pick_prev_close({"prevDailyBar": bar(YDAY, 10.0)}, TODAY) == 10.0


def test_prev_close_missing_both_or_odd():
    assert pick_prev_close({}, TODAY) is None
    assert pick_prev_close({"dailyBar": None, "prevDailyBar": {"t": "junk", "c": "x"}}, TODAY) is None
    assert pick_prev_close({"dailyBar": bar("2026-09-30T04:00:00Z", 5.0)}, TODAY) is None  # today's bar only


# -- stage 1 -------------------------------------------------------------------

def snap(price, prev=10.0, age_min=2, trade_day_offset=0):
    t = NOW - timedelta(minutes=age_min) - timedelta(days=trade_day_offset)
    return {"last_price": price, "last_trade_time": t, "dailyBar": bar(YDAY, prev), "prevDailyBar": bar(DBY, prev * 0.9)}


def test_stage1_filters_and_ranks():
    snaps = {
        "LOWP": snap(0.9, prev=0.5),            # below min price
        "STALE": snap(13.0, age_min=45),        # last trade too old
        "YDAY": snap(13.0, trade_day_offset=1),  # no trade today
        "SMALL": snap(10.9),                    # +9% < 10%
        "EDGE": snap(11.0),                     # exactly +10%
        "BIG": snap(15.0),                      # +50%
        "MID": snap(12.0),                      # +20%
        "NOPRICE": {"last_price": None, "last_trade_time": NOW},
        "ODD": {"last_price": "abc", "last_trade_time": NOW, "dailyBar": 5},
        "NOPREV": {"last_price": 20.0, "last_trade_time": NOW},
        "NONE": None,
    }
    out = stage1_candidates(snaps, CFG, NOW)
    assert [c["ticker"] for c in out] == ["BIG", "MID", "EDGE"]
    assert out[0]["gap_pct"] == pytest.approx(50.0) and out[0]["prev_close"] == 10.0


def _universe_provider(n_gappers, **kw):
    snaps = {f"G{i:02d}": snap(11.0 + i * 0.1) for i in range(n_gappers)}
    snaps["QUIET"] = snap(10.1)
    return FakeProvider(snaps, {}, universe=list(snaps), **kw)


def test_cap_respected_and_total_reported():
    prov = _universe_provider(8)
    cfg = {**CFG, "max_stage2_candidates": 3}
    out = PremarketMomentumScanner().run([], cfg, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "ok"
    assert [r["ticker"] for r in out.rows] == ["G07", "G06", "G05"]  # highest gaps first
    assert out.message.startswith("Swept 9 symbols; 8 gapped >=10% (checked the top 3); 0 flagged")


def test_zero_candidates_is_ok_with_message_and_no_rows():
    prov = FakeProvider({"A": snap(10.2)}, {}, universe=["A"])
    out = PremarketMomentumScanner().run([], CFG, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "ok" and out.rows == [] and out.message == "Swept 1 symbols; 0 gapped >=10%; 0 flagged"


def test_chunk_failure_is_skipped_with_warning():
    names = [f"S{i}" for i in range(1200)]
    snaps = {n: snap(10.0) for n in names}
    snaps["S700"] = snap(15.0)
    prov = FakeProvider(snaps, {}, universe=names, failing_snapshot_calls={0})
    out = PremarketMomentumScanner().run([], CFG, RunContext(intraday_provider=prov, now=NOW))
    assert prov.snapshot_sizes == [500, 500, 200]
    assert out.status == "ok" and "1 snapshot chunk(s) failed and were skipped" in out.message


def test_all_chunks_failing_is_an_error():
    prov = FakeProvider({}, {}, universe=["A", "B"], failing_snapshot_calls={0})
    out = PremarketMomentumScanner().run([], CFG, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "error"


# -- end to end ----------------------------------------------------------------

def test_demo_universe_end_to_end():
    prov = make_demo_universe_provider(NOW)
    assert len(prov.universe) == 300
    out = PremarketMomentumScanner().run([], CFG, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "ok"
    assert out.message == "Swept 300 symbols; 6 gapped >=10%; 3 flagged"
    flagged = [r["ticker"] for r in out.rows if r["flagged"]]
    assert len(flagged) == 3 and set(flagged) == {s.ticker for s in out.signals}
    assert len(out.rows) == 6  # only stage-2 candidates, not the 300
    r = out.rows[0]
    assert r["universe_mode"] == "whole_market" and r["stage1_gap"] > 10 and r["sip_prev_close"] is not None


def _one(today_vol, hist_vol, price=12.0, prev=10.0):
    snaps = {"AAA": snap(price, prev=prev)}
    bars = {"AAA": make_premarket_bars(NOW, today_vol, hist_vol, price=price)}
    d = pd.DatetimeIndex([datetime(2026, 9, 29, tzinfo=ET)])
    daily = {"AAA": pd.DataFrame({"open": prev, "high": prev, "low": prev, "close": prev, "volume": 1e6}, index=d)}
    return FakeProvider(snaps, bars, daily=daily, universe=["AAA"])


def run_one(prov, cfg=CFG):
    return PremarketMomentumScanner().run([], cfg, RunContext(intraday_provider=prov, now=NOW))


def test_flag_requires_relvol_gap_and_volume_floor():
    assert run_one(_one(600_000, 100_000)).signals  # 6x, +20%
    assert not run_one(_one(450_000, 100_000)).signals  # 4.5x
    assert not run_one(_one(40_000, 5_000)).signals  # 8x but only 40k shares
    out = run_one(_one(600_000, 100_000), {**CFG, "min_gap_pct": 5.0})
    assert out.signals


def test_sip_permission_error_falls_back_in_whole_market():
    prov = _one(600_000, 100_000)
    prov.permission_error_feeds = ("sip",)
    out = run_one(prov)
    assert out.status == "ok" and "IEX-only volume" in out.message and out.message.startswith("Swept 1 symbols")
    assert ("bars", "5Min", "iex") in prov.calls


def test_auth_error_status_error_without_key_text():
    class Rejecting(FakeProvider):
        def list_symbols(self, base_url=None):
            raise ProviderAuthError("Alpaca rejected the API keys")

    out = run_one(Rejecting({}, {}))
    assert out.status == "error" and "rejected" in out.message and "test-key" not in out.message


def test_prev_close_mismatch_is_not_flagged():
    prov = _one(600_000, 100_000, price=12.0, prev=10.0)
    prov._daily["AAA"].loc[:, "close"] = 5.0  # SIP (split-adjusted) says 5, IEX says 10
    out = run_one(prov)
    assert out.rows[0]["error"] == "prev close mismatch (split?)"
    assert not out.rows[0]["flagged"] and out.signals == []


def test_watchlist_mode_unchanged_and_get_universe():
    prov = _one(600_000, 100_000)
    cfg = {**CFG, "universe_mode": "watchlist"}
    out = PremarketMomentumScanner().run(["AAA"], cfg, RunContext(intraday_provider=prov, now=NOW))
    assert out.message == "" and out.rows[0]["universe_mode"] == "watchlist" and out.rows[0]["stage1_gap"] is None
    assert ("list_symbols",) not in prov.calls
    assert get_universe(cfg, prov, {"momentum": ["x", "y"]}) == ["X", "Y"]
    assert get_universe(CFG, prov) == ["AAA"]


def test_default_universe_mode_is_whole_market():
    assert CFG["universe_mode"] == "whole_market" and CFG["min_gap_pct"] == 10.0


# -- Alpaca provider: chunking, 429, assets list --------------------------------

class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text

    def json(self):
        return self._p


def _prov():
    return AlpacaProvider(key="test-key", secret="test-secret")


def test_1200_symbols_make_three_snapshot_calls(monkeypatch):
    seen = []
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k: seen.append(params["symbols"]) or Resp(200, {}))
    _prov().snapshots([f"S{i}" for i in range(1200)])
    assert [len(s.split(",")) for s in seen] == [500, 500, 200]


def test_429_once_then_success_is_retried(monkeypatch):
    replies = [Resp(429), Resp(200, {"AAA": {"latestTrade": {"p": 5, "t": "2026-09-30T12:00:00Z"}}})]
    monkeypatch.setattr(requests, "get", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(alpaca_mod.time, "sleep", lambda s: None)
    assert _prov().snapshots(["AAA"])["AAA"]["last_price"] == 5.0


def test_snapshot_keeps_daily_bars(monkeypatch):
    payload = {"AAA": {"latestTrade": {"p": 5, "t": "2026-09-30T12:00:00Z"}, "dailyBar": bar(YDAY, 4), "prevDailyBar": bar(DBY, 3)}}
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(200, payload))
    s = _prov().snapshots(["AAA"])["AAA"]
    assert s["dailyBar"]["c"] == 4 and s["prevDailyBar"]["c"] == 3


ASSETS = [
    {"symbol": "AAPL", "exchange": "NASDAQ", "tradable": True},
    {"symbol": "BRK.B", "exchange": "NYSE", "tradable": True},
    {"symbol": "ABCDW1", "exchange": "NASDAQ", "tradable": True},
    {"symbol": "TOOLONG", "exchange": "NYSE", "tradable": True},
    {"symbol": "DEAD", "exchange": "NYSE", "tradable": False},
    {"symbol": "OTCX", "exchange": "OTC", "tradable": True},
    {"symbol": "SPY", "exchange": "ARCA", "tradable": True},
    {"symbol": "ZM1", "exchange": "NASDAQ", "tradable": True},
    {"symbol": "BATSY", "exchange": "BATS", "tradable": True},
    {"symbol": "F", "exchange": "NYSE", "tradable": True},
    {"symbol": "XYZ", "exchange": "AMEX", "tradable": True},
    "garbage",
]


def test_assets_filtering():
    assert filter_assets(ASSETS) == ["AAPL", "BATSY", "F", "SPY", "XYZ"]


def test_assets_cache_ttl_and_url(monkeypatch):
    monkeypatch.setattr(alpaca_mod, "_ASSETS_CACHE", {})
    calls, clock = [], [1000.0]
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k: calls.append((url, params)) or Resp(200, ASSETS))
    monkeypatch.setattr(alpaca_mod.time, "time", lambda: clock[0])
    p = _prov()
    assert p.list_symbols() == ["AAPL", "BATSY", "F", "SPY", "XYZ"]
    assert calls[0][0] == "https://paper-api.alpaca.markets/v2/assets"
    assert calls[0][1] == {"status": "active", "asset_class": "us_equity"}
    clock[0] += 3600
    p.list_symbols()
    assert len(calls) == 1  # cached within TTL
    clock[0] += 12 * 3600
    p.list_symbols()
    assert len(calls) == 2
    p.list_symbols("https://example.test")
    assert calls[-1][0] == "https://example.test/v2/assets"
