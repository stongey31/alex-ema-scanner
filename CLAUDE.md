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
  `core.config.get_universe` (watchlist only). The pre-market scanner's
  `universe_mode` defaults to `whole_market`: its `run()` ignores `tickers` and
  builds the universe itself via `scanners.premarket_momentum.get_universe`
  (`provider.list_symbols`); in `"watchlist"` mode it uses the passed tickers.
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

- **Alpaca specifics**: the whole-market facts below were checked live (after
  hours) by Mike's assistant with paper keys; tests still use canned responses
  only. Still unverified: real pre-market IEX coverage, real 429 behaviour, and
  how Streamlit Cloud copes with ~26 sequential calls per run.
- **Pre-market previous-close trap.** In a snapshot, `dailyBar` may be absent or
  be *yesterday's* bar, in which case `prevDailyBar` is the day *before*
  yesterday. Never read `prevDailyBar` blindly: use `pick_prev_close()`, which
  keeps bars dated strictly before today (ET) and takes the latest one.
- **`feed=sip` on snapshots returns 403** ("subscription does not permit
  querying recent SIP data") on the free plan, while SIP *bars* older than
  ~15 min work. So price/gap = IEX snapshots, volume = delayed SIP bars.
- **The assets list is on the Trading API host** (`paper-api.alpaca.markets`,
  `/v2/assets`), not `data.alpaca.markets`; same key headers. Configurable via
  `assets_base_url`; cached 12 h per process (`_ASSETS_CACHE`). Snapshot chunks
  are 500 symbols, sequential, 429 retried with backoff. Raw IEX gaps are noisy
  (splits look like 700-3000%), so stage 2 re-checks prev close against split-
  adjusted SIP daily bars (>20% off => "prev close mismatch (split?)", not flagged).
  No ETF filter exists (not reliably derivable from the assets endpoint).

- **Pre-market history only counts sessions that have bars in the window.** A
  past session with zero IEX/SIP bars before the cutoff is absent from the
  bars, not counted as 0, which can inflate the average slightly for thin names.

- **`worktrees/` was not in `.gitignore` originally**; it is now.

- **`scanners/early_runner.py`** is a daily-lane scanner that builds its own whole-market universe (like
  `premarket_momentum`) and uses `ctx.intraday_provider or AlpacaProvider()`. It skips a stock if the daily
  bars' last date differs from the snapshot's `dailyBar` date (stale bars), uses yfinance only for stocks that
  already passed the volume/trend test (`_fundamentals`, monkeypatched in tests), and only flags when BOTH market
  cap and float are known and small. `app.py` reads optional `candidates_label` (in `ScanOutput.extra`) and
  `whole_market_caption` (scanner attribute). On Windows, `tests/test_pluggability.py` needs `PYTHONUTF8=1`
  (a console-encoding issue with the warning sign), and a deep virtualenv path can silently drop Streamlit's `proto/` folder.
