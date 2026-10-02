"""Real-network check that refactor == legacy on the live mega_caps list. Run with: pytest -m live"""

import pytest

from core.config import load_watchlists
from scanners import ma_bounce
from tests.legacy import screener_v1 as legacy


@pytest.mark.live
def test_live_equivalence():
    tickers = load_watchlists()["mega_caps"]
    old, new = legacy.scan_watchlist(tickers), ma_bounce.scan_watchlist(tickers)
    if old != new:  # data can tick between the two fetches; retry once
        old, new = legacy.scan_watchlist(tickers), ma_bounce.scan_watchlist(tickers)
    if all(r["error"] for r in new):
        pytest.skip("network/Yahoo unavailable: every ticker errored")
    assert old == new
