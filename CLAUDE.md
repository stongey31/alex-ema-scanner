# alex-ema-scanner

Personal project for Alex (non-technical, finance background). Screens
mega-cap tickers for bounces off the 50/200 EMA and SMA (four separate
signals, RSI < 30 filter on all, recent-earnings filter on the 200 EMA only)
and alerts to Discord.
See README.md for the full logic definition and setup checklist.

## Gotchas found while building this

- **Streamlit's `use_container_width` is already past its stated removal
  date.** It was deprecated with a removal date of 2025-12-31; as of the
  installed version (1.64.0) it still works but warns loudly. Use
  `width="stretch"` / `width="content"` instead everywhere in `app.py` --
  already done, but keep doing this in any new widget code rather than
  reintroducing `use_container_width`.

- **yfinance's `get_earnings_dates()` timezone handling is inconsistent**
  (sometimes tz-aware in `America/New_York`, sometimes naive, and can be
  `None`/empty for tickers with no earnings calendar, e.g. ETFs like SPY).
  `screener.py`'s `_check_earnings()` normalizes everything to tz-naive UTC
  before comparing -- don't compare `pd.Timestamp.now()` directly against
  the raw index without going through that normalization, or you'll hit
  intermittent tz-aware/naive comparison `TypeError`s depending on the
  ticker.

- **`requirements.txt` is intentionally unpinned** (per the original spec).
  This means `pip install -r requirements.txt` on a fresh deploy could pull
  a newer pandas/streamlit/yfinance later and behave slightly differently
  (as happened with the `use_container_width` warning above). If something
  breaks after a redeploy that worked before, check for upstream version
  drift first.

- **The daily GitHub Actions job commits `data/watchlist.json` back to the
  repo** (the `"alerted"` dedup log) with `[skip ci]`. Without that step the
  log is lost when the runner shuts down and every bounce re-alerts daily --
  which is how the original version shipped. Pull before editing locally.

- **`runner.py` only records an alert after `send_alert()` returns True.**
  Don't move the dedup write ahead of the send; with no webhook configured
  (or a failed post) the alert would be marked sent and never delivered.

- **RSI is hand-rolled Wilder smoothing** (SMA-seeded, then recursive), not
  `ewm(alpha=1/14)`. The ewm shortcut seeds differently and drifts from
  TradingView/StockCharts values. Verified against the StockCharts reference
  table to within rounding.
