You are building a complete, working Python project on this Linux homelab machine. This is a personal favor project: "Mike" (the user you would be reporting to) is technical and is building this for his friend "Alex," who is not technical but knows the trading/finance side. Alex wants a tool that screens mega-cap stocks for a specific technical setup and pings his Discord when it finds one.

## Where to build it
Build in the current directory: ~/projects/alex-ema-scanner (already created). Initialize a local git repo here (git init) if not already one, and make sensible commits as you go. Do NOT create a GitHub repo, do NOT push to any remote, do NOT sign into or create any account anywhere (GitHub, Streamlit, Discord, etc.) -- none of that is available to you and none of it should be attempted. This is 100% a local build task. Use python3 and a local virtualenv (.venv) for dependencies.

## What it does (the strategy)
Screens a watchlist of mega-cap stocks for: price recently bounced off its 200-day EMA, shortly after an earnings report. Alerts to Discord and shows a dashboard.

## Required file structure and behavior

**screener.py** -- the analysis engine:
- Use yfinance to pull 1 year of daily historical data per ticker.
- Calculate the 200-day EMA: hist['Close'].ewm(span=200, adjust=False).mean().
- Bounce detection (important -- do this properly, not just a same-day proximity check): Look at the last 10 trading sessions (configurable). If the close on ANY of those days was within the configured proximity threshold (default 2.0%) of that day's 200 EMA, mark it as "touched" and record that day's close plus EMA. Confirm an actual bounce (is_bounce = True) only if, in addition to having touched, TODAY's close is (a) above today's 200 EMA, AND (b) higher than the close on the touch day. This confirms an actual upward reversal off the average, not just current proximity to it or a continued decline through it. Document this logic clearly in a README so Alex and Mike understand the definition being used (a "pullback to a rising average, then reversal upward" pattern -- not the mirror-image resistance-rejection case).
- Earnings check: use yf.Ticker.get_earnings_dates(). Normalize datetime timezones carefully to avoid comparison errors (this yfinance method is known to be inconsistent -- handle None/empty results gracefully). earnings_recent = True if an earnings date falls within the last N days (default 7, configurable).
- Return structured dicts per ticker: ticker, current_close, ema200, dist_pct, is_bounce, earnings_recent, last_earnings_date.
- One failing ticker (bad data, network hiccup, missing earnings data) must never halt the whole scan -- catch and log per-ticker, continue.

**alerts.py** -- Discord dispatcher:
- send_alert(setup_data) posts a Discord embed via webhook (using requests).
- Embed shows: ticker, current price, 200 EMA, percent distance (color-coded: green if price above EMA, red if below), days since last earnings, and a direct link https://www.tradingview.com/symbols/{ticker}.
- Read the webhook URL from the DISCORD_WEBHOOK_URL environment variable (support a local .env file via python-dotenv for local testing). If it is empty/missing, log the alert to console instead of throwing -- never crash on a missing webhook.

**runner.py** -- headless entry point (this is what a scheduled job will run once a day):
- Reads the actual monitored ticker list from data/watchlist.json (see below) -- this is the persistent, "real" list that drives automated alerts. It is NOT the same as anything typed into the dashboard sidebar in a browser session.
- Runs screener.py across that list, filters for tickers where BOTH is_bounce and earnings_recent are true.
- Deduplication (important): maintain an "already alerted" log inside data/watchlist.json (e.g. a per-ticker last_alerted_earnings_date field). Only fire a Discord alert for a ticker plus earnings-date combination once -- if the same earnings event is still within the lookback window tomorrow and the stock is still near the EMA, do NOT re-alert for that same earnings date. Update the log file after sending. Commit is not required (no git remote), just persist the JSON file correctly.

**data/watchlist.json** -- the single persistent source of truth for the automated scan:
- Structure roughly: {"tickers": ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AMD","AVGO","NFLX"], "proximity_threshold_pct": 2.0, "earnings_lookback_days": 7, "bounce_lookback_days": 10, "alerted": {}} where "alerted" maps ticker to last alerted earnings date string.
- This file is meant to be hand-edited by Alex directly in GitHub's website later (plain text edit) whenever he wants to permanently change the watchlist or settings -- document this clearly in the README. Do not build any mechanism for the dashboard to write back to this file automatically; keep it simple.

**app.py** -- Streamlit dashboard (this will later be deployed to Streamlit Community Cloud, but for now just needs to run correctly locally via streamlit run app.py):
- Page config: wide mode, title "Post-Earnings 200 EMA Scanner".
- Sidebar: loads defaults from data/watchlist.json but allows ad-hoc, session-only changes (add/remove tickers via multiselect or text input, adjust proximity threshold and earnings lookback) purely for interactive browsing -- these changes are NOT persisted anywhere and do not affect the automated runner.py alerts. Make this distinction clear in the UI itself (e.g. a caption noting that changes there are for browsing only, and that data/watchlist.json on GitHub is what to edit to change the automated alert list). Include a "Run Scan" button with a spinner.
- Main panel: top metrics (total scanned, matching setups count), a summary table of all scanned tickers (current price, 200 EMA, percent spread, bounce status, recent earnings status -- highlight matching rows), and a detail view -- when a ticker is selected, an interactive Plotly candlestick chart of the last 6 months with the 200 EMA overlaid in orange and working tooltips.

**requirements.txt**: yfinance, pandas, requests, streamlit, plotly, python-dotenv.

**.gitignore**: exclude .env, __pycache__/, .venv/, etc.

**.github/workflows/daily-scan.yml**: a GitHub Actions workflow (write the YAML file, do not attempt to activate or register it anywhere) that: runs on a schedule (cron "30 21 * * 1-5", roughly 4:30-5:30pm ET depending on DST -- note the DST caveat in a comment and in the README), checks out the repo, sets up Python, installs requirements.txt, and runs "python runner.py", with DISCORD_WEBHOOK_URL sourced from a GitHub Actions repository secret of the same name (secrets.DISCORD_WEBHOOK_URL).

**README.md** -- must include two clearly separate sections:
1. What the tool does and the exact bounce/earnings logic definition (so Alex understands what "bounce" means here).
2. A plain-language, click-by-click, no-jargon "one-time setup checklist" for going from this local code to actually live, covering: creating a free GitHub account (if Alex does not have one) and pushing this code to a new repo there; signing into Streamlit Community Cloud using that same GitHub login and deploying app.py from the repo; creating the Discord webhook inside Alex's own Discord server; adding DISCORD_WEBHOOK_URL as a secret in BOTH the Streamlit app's settings AND the GitHub repo's Actions secrets; and how to hand-edit data/watchlist.json on GitHub's website later to change the monitored tickers or thresholds. Write this section for someone who has never done any of this before -- no assumed jargon.

If there are genuine non-obvious setup gotchas worth remembering (matching this homelab's convention of a per-project CLAUDE.md), add a brief one -- but only if real content emerges, not proactively or as boilerplate.

## Verification (do this -- do not just claim it works)
- Actually run screener.py's core logic against real live Yahoo Finance data for the 10 default tickers and confirm it returns structured results without crashing, including at least one ticker whose earnings-date lookup comes back empty or None (to confirm that code path does not crash).
- Actually invoke runner.py locally with no DISCORD_WEBHOOK_URL set and confirm it logs to console instead of throwing an unhandled exception.
- Actually start "streamlit run app.py" locally (headless mode, e.g. streamlit run app.py --server.headless true) and confirm it boots without import or runtime errors, and that a scan can run when triggered.
- Fix anything you find during this verification before considering the task done. Do not report success based on "the code looks right" alone.

## Report back
When finished, give a clear summary: what was built, what you verified and how, any assumptions or defaults you had to make beyond what is specified above, and the exact next steps (pointing at the README's setup checklist). Flag anything that seems genuinely uncertain or risky (e.g., if yfinance behaved unreliably during your testing) rather than glossing over it.
