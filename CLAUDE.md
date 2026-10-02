# alex-ema-scanner

Personal project for Alex (non-technical, finance background). Screens
mega-cap tickers for bounces off the 50/200 EMA and SMA (four separate
signals, RSI < 30 filter on all, recent-earnings filter on the 200 EMA only)
and alerts to Discord. A second scanner finds pre-market gappers on heavy
volume (Alpaca data). See README.md for the logic definitions and setup checklist.

## Layout / plugin contract

- `scanners/<id>.py` = one scanner (file name = ID). Exactly one `Scanner`
  subclass per file (`core/scanner_base.py`): `run(tickers, config, ctx) ->
  ScanOutput(status, message, rows, signals)`; optional `table`, `format_alert`,
  `build_alert_messages`, `render_detail`. Files starting with `_` or `test` are
  skipped; `scanners/_template.py` is the copy-me example. A broken file becomes
  a "⚠" tab / `BROKEN` line, never a crash (`core/registry.py`).
- Settings: `default_config` in the class, overridden by `data/config/<id>.json`
  (`"enabled": false` turns it off). Tickers: `data/watchlists.json`, chosen via
  `get_universe(cfg, provider)` in `core/config.py` (hook for a future
  whole-market mode).
- Alert dedup lives in `data/alert_log.json` (`{scanner_id: {"TICKER:signal":
  episode_id}}`), written by `runner.py` only after `post_discord()` returns True.
  The old `"alerted"` map in `data/watchlist.json` was migrated here.
- `runner.py --lane daily|intraday [--only ID] [--dry-run] [--et-window ...]`;
  workflows: `daily-scan.yml`, `premarket-scan.yml` (shared `alert-log`
  concurrency group).
- `tests/legacy/` holds FROZEN copies of the pre-refactor screener/alerts used
  by the equivalence tests -- never edit them.

## Gotchas found while building this

- **Streamlit's `use_container_width` is already past its stated removal
  date.** It was deprecated with a removal date of 2025-12-31; as of the
  installed version (1.64.0) it still works but warns loudly. Use
  `width="stretch"` / `width="content"` instead everywhere in `app.py` and `core/ui.py` --
  already done, but keep doing this in any new widget code rather than
  reintroducing `use_container_width`.

- **yfinance's `get_earnings_dates()` timezone handling is inconsistent**
  (sometimes tz-aware in `America/New_York`, sometimes naive, and can be
  `None`/empty for tickers with no earnings calendar, e.g. ETFs like SPY).
  `scanners/ma_bounce.py`'s `_check_earnings()` normalizes everything to tz-naive UTC
  before comparing -- don't compare `pd.Timestamp.now()` directly against
  the raw index without going through that normalization, or you'll hit
  intermittent tz-aware/naive comparison `TypeError`s depending on the
  ticker.

- **`requirements.txt` is now pinned (`==`) except `yfinance`** (`>=` only, so
  Yahoo-compat fixes land). Target runtime is Python 3.12 (Actions and Streamlit
  Cloud). Dev-only deps (pytest) live in `requirements-dev.txt`. Bump pins
  deliberately and run `pytest -q`.
- **`pd.Timestamp.utcnow()` in `_check_earnings` is deprecated in pandas 3**
  (emits `Pandas4Warning`, removed in pandas 4). It was left untouched because
  ma_bounce must have no behavior change; fix it (`Timestamp.now("UTC")`) on its
  own, with the equivalence tests, before ever unpinning pandas.

- **The GitHub Actions jobs commit `data/alert_log.json` back to the
  repo** (the dedup log) with `[skip ci]`. Without that step the
  log is lost when the runner shuts down and every bounce re-alerts daily --
  which is how the original version shipped. Pull before editing locally.

- **`runner.py` only records an alert after `post_discord()` returns True.**
  Don't move the dedup write ahead of the send; with no webhook configured
  (or a failed post) the alert would be marked sent and never delivered.

- **RSI is hand-rolled Wilder smoothing** (SMA-seeded, then recursive), not
  `ewm(alpha=1/14)`. The ewm shortcut seeds differently and drifts from
  TradingView/StockCharts values. Verified against the StockCharts reference
  table to within rounding.

- **Streamlit number inputs need one numeric type.** `core/ui.py` casts
  `setting_meta` min/max/step to the type of the current value (e.g. the int
  `rsi_oversold_threshold: 30` with float-looking limits would otherwise raise).

- **Alpaca specifics are unverified assumptions** (no keys existed while
  building): snapshot/bars JSON shapes, free-plan SIP being delayed ~15 min,
  IEX pre-market coverage. `core/providers/alpaca.py` parses defensively and
  tests use canned responses only. Re-check against real responses once keys exist.

- **Pre-market history only counts sessions that have bars in the window.** A
  past session with zero IEX/SIP bars before the cutoff is absent from the
  bars, not counted as 0, which can inflate the average slightly for thin names.

- **`worktrees/` was not in `.gitignore` originally**; it is now.
