"""One-off LIVE smoke sweep of the whole-market pre-market scanner.

Makes real (read-only) Alpaca market-data calls: ~1 assets call + ~26 snapshot
calls + a few bars calls. Never posts to Discord and never writes files.

Usage (type the keys yourself in your shell; they are never printed):
    export ALPACA_API_KEY=...        # paper keys are fine
    export ALPACA_API_SECRET=...
    .venv/bin/python scripts/live_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.providers.alpaca import AlpacaProvider  # noqa: E402
from core.scanner_base import RunContext  # noqa: E402
from scanners.premarket_momentum import PremarketMomentumScanner, get_universe, stage1_candidates  # noqa: E402


def main() -> int:
    prov = AlpacaProvider()
    if not prov.is_configured():
        print("Set ALPACA_API_KEY and ALPACA_API_SECRET in your environment first.")
        return 1
    scanner = PremarketMomentumScanner()
    cfg = dict(scanner.default_config)
    ctx = RunContext(intraday_provider=prov)
    print(f"Now (ET): {ctx.now:%Y-%m-%d %H:%M}")

    # Stage 1 on its own, so the top gappers can be printed.
    try:
        universe = get_universe(cfg, prov)
        snaps = {}
        for i in range(0, len(universe), 500):
            snaps.update(prov.snapshots(universe[i : i + 500], feed=cfg["price_feed"]))
    except Exception as e:  # keep keys out of the output: print only the type and message
        print(f"Stage 1 failed: {type(e).__name__}: {e}")
        return 1
    gappers = stage1_candidates(snaps, cfg, ctx.now)
    print(f"Universe: {len(universe):,} symbols; snapshots returned: {len(snaps):,}; gappers >= {cfg['min_gap_pct']:g}%: {len(gappers)}")
    print("\nTop 10 stage-1 gappers (IEX, noisy):")
    for c in gappers[:10]:
        print(f"  {c['ticker']:<6} last ${c['last_price']:.2f}  prev ${c['prev_close']:.2f}  gap {c['gap_pct']:+.1f}%")

    # Full two-stage run (repeats the sweep: ~26 more calls).
    out = scanner.run([], cfg, ctx)
    print(f"\nScanner status: {out.status}\n{out.message}")
    flagged = [r for r in out.rows if r.get("flagged")]
    print(f"\nFlagged rows ({len(flagged)}):")
    for r in flagged:
        print(f"  {r['ticker']:<6} gap {r['gap_pct']:+.1f}%  RelVol {r['rel_volume']:.1f}x  vol {r['volume_so_far']:,.0f}  ${r['last_price']:.2f}")
    errs = [r for r in out.rows if r.get("error")]
    if errs:
        print(f"\nCandidates with a data problem ({len(errs)}):")
        for r in errs[:10]:
            print(f"  {r['ticker']:<6} {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
