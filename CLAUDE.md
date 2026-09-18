# alex-ema-scanner

Personal project for Alex (non-technical, finance background). Screens
mega-cap tickers for a post-earnings 200 EMA bounce and alerts to Discord.
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
