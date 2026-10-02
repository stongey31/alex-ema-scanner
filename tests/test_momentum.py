from datetime import datetime

import pandas as pd
import pytest

from core.providers.base import ProviderAuthError
from core.providers.fake import FakeProvider, make_demo_provider, make_premarket_bars
from core.scanner_base import ET, RunContext
from scanners.premarket_momentum import PremarketMomentumScanner, compute_row

NOW = datetime(2026, 9, 30, 8, 30, tzinfo=ET)  # a Wednesday
CFG = {**PremarketMomentumScanner.default_config, "min_gap_pct": 3.0}


def _row(today_vol, hist_vol, gap_pct=4.0, cfg=CFG, sessions=10, price=10.0, bars=None):
    if bars is None:
        bars = make_premarket_bars(NOW, today_vol, hist_vol, sessions=sessions, price=price)
    prev = price / (1 + gap_pct / 100)
    return compute_row("TEST", price, prev, bars, NOW, cfg)


def test_flags_at_6x_and_4pct_gap():
    r = _row(600_000, 100_000)
    assert r["rel_volume"] == pytest.approx(6.0, abs=0.02)
    assert r["gap_pct"] == pytest.approx(4.0, abs=0.01)
    assert r["flagged"] and r["error"] is None
    assert r["volume_as_of"] == "08:14" and r["volume_feed_used"] == "sip"


def test_not_flagged_below_relvol():
    r = _row(490_000, 100_000)
    assert r["rel_volume"] == pytest.approx(4.9, abs=0.02)
    assert not r["flagged"]


@pytest.mark.parametrize("gap", [1.0, -3.0])
def test_not_flagged_small_or_negative_gap(gap):
    assert not _row(600_000, 100_000, gap_pct=gap)["flagged"]


def test_default_gap_threshold_is_10_percent():
    assert PremarketMomentumScanner.default_config["min_gap_pct"] == 10.0
    assert not _row(600_000, 100_000, gap_pct=9.0, cfg=PremarketMomentumScanner.default_config)["flagged"]
    assert _row(600_000, 100_000, gap_pct=11.0, cfg=PremarketMomentumScanner.default_config)["flagged"]


def test_not_flagged_when_volume_below_minimum():
    r = _row(40_000, 5_000)  # 8x but only 40k shares
    assert r["rel_volume"] >= 5 and not r["flagged"]


def test_zero_average_gives_none_relvol():
    r = _row(600_000, 0)
    assert r["rel_volume"] is None and not r["flagged"]


def test_too_few_sessions_is_an_error():
    r = _row(600_000, 100_000, sessions=3)
    assert r["error"] == "not enough history" and not r["flagged"]


def test_cutoff_ignores_later_bars_today_and_in_history():
    bars = make_premarket_bars(NOW, 600_000, 100_000)
    base = compute_row("T", 10.0, 9.6, bars, NOW, CFG)
    extra = bars.copy()
    # bars at/after the 08:14 cutoff: huge volume on every day, today included
    late = extra.index.hour * 60 + extra.index.minute >= 8 * 60 + 14
    extra.loc[late, "volume"] = 10_000_000
    assert compute_row("T", 10.0, 9.6, extra, NOW, CFG) == base
    # bars before the 04:00 session start are ignored too
    early = bars.iloc[:3].copy()
    early.index = early.index - pd.Timedelta(hours=2)
    with_early = pd.concat([bars, early]).sort_index()
    assert compute_row("T", 10.0, 9.6, with_early, NOW, CFG) == base


def test_run_end_to_end_with_fake_provider():
    scanner = PremarketMomentumScanner()
    prov = make_demo_provider(["AAA", "BBB", "CCC"], NOW)
    out = scanner.run(["AAA", "BBB", "CCC"], scanner.default_config, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "ok" and out.message == ""
    by = {r["ticker"]: r for r in out.rows}
    assert by["AAA"]["flagged"] and not by["BBB"]["flagged"] and not by["CCC"]["flagged"]
    assert [s.key for s in out.signals] == ["AAA:gap"]
    s = out.signals[0]
    assert s.episode_id == s.episode_start == "2026-09-30"
    assert set(s.data) == {"gap", "relvol", "price"}


def test_sip_permission_error_falls_back_to_iex():
    scanner = PremarketMomentumScanner()
    prov = make_demo_provider(["AAA", "BBB", "CCC"], NOW)
    prov.permission_error_feeds = ("sip",)
    out = scanner.run(["AAA", "BBB", "CCC"], scanner.default_config, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "ok"
    assert "IEX-only volume" in out.message
    assert all(r["volume_feed_used"] == "iex" for r in out.rows)
    assert out.rows[0]["volume_as_of"] == "08:30"  # delay dropped to 0
    assert ("bars", "5Min", "iex") in prov.calls


def test_auth_error_is_status_error_without_key_text():
    class Rejecting(FakeProvider):
        def snapshots(self, symbols, feed="iex"):
            raise ProviderAuthError("Alpaca rejected the API keys")

    prov = Rejecting({}, {})
    out = PremarketMomentumScanner().run(["AAA"], PremarketMomentumScanner.default_config, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "error" and "rejected" in out.message
    assert "test-key" not in out.message


def test_missing_ticker_data_is_a_row_error_not_a_crash():
    scanner = PremarketMomentumScanner()
    prov = make_demo_provider(["AAA"], NOW)
    out = scanner.run(["AAA", "ZZZ"], scanner.default_config, RunContext(intraday_provider=prov, now=NOW))
    assert out.status == "ok" and out.rows[1]["error"]


def test_alert_messages_split_at_25_fields():
    from core.scanner_base import Signal

    scanner = PremarketMomentumScanner()
    hits = {f"T{i}": [Signal(f"T{i}", f"T{i}:gap", "2026-09-30", "2026-09-30", {"gap": 12.0, "relvol": 6.0, "price": 5.0})] for i in range(30)}
    msgs = scanner.build_alert_messages(hits, {})
    assert [len(p["embeds"][0]["fields"]) for p, _ in msgs] == [25, 5]
    assert [len(s) for _, s in msgs] == [25, 5]
