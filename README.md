# Post-Earnings 200 EMA Scanner

A small tool that screens a watchlist of mega-cap stocks for one specific
technical setup, and pings a Discord channel when it finds one:

> **Price recently bounced off its 200-day EMA, shortly after an earnings report.**

There's a Streamlit dashboard (`app.py`) for browsing the scan interactively,
and a headless script (`runner.py`) meant to be run once a day by a scheduled
job (GitHub Actions, see `.github/workflows/daily-scan.yml`) that fires the
actual Discord alerts.

---

## 1. What it does, and exactly what "bounce" means

### The bounce logic

For each ticker, the scanner pulls 1 year of daily price history and computes
the 200-day EMA over the whole series:

```python
ema200 = hist['Close'].ewm(span=200, adjust=False).mean()
```

Then it looks at the **last 10 trading sessions** (configurable via
`bounce_lookback_days`, includes today) and checks each day's closing price
against *that day's own* 200 EMA value:

1. **Touch**: if any of those days closed within **2.0%** (configurable via
   `proximity_threshold_pct`) of its 200 EMA, the ticker "touched" the
   average. If more than one day in the window touched, the day with the
   **lowest close** is used as the reference touch day -- i.e. the deepest
   point of the pullback.
2. **Bounce confirmation**: `is_bounce` is only `True` if, in addition to
   having touched, **today's** close is:
   - **(a)** above today's 200 EMA, **and**
   - **(b)** higher than the close on that touch day.

Why both conditions? Touching the average alone tells you nothing -- price
could still be sliding through it on the way down. Requiring today's close to
be both above the EMA *and* above the touch-day close confirms an actual
upward reversal off the average: a **pullback to a rising-ish average,
followed by a bounce upward.**

This is deliberately **not symmetric**. The mirror-image case -- price
rallying up into the 200 EMA from below and getting rejected back down -- is
NOT what this tool looks for, and will not trigger `is_bounce`.

### The earnings logic

`earnings_recent` is `True` if the ticker's most recent **past** earnings
date (from `yfinance`'s `get_earnings_dates()`) falls within the last **7
days** (configurable via `earnings_lookback_days`).

yfinance's earnings-calendar data is known to be inconsistent -- sometimes
timezone-aware, sometimes not, sometimes empty, sometimes only future
estimated dates with no history. `screener.py` handles all of that
defensively: a missing or malformed earnings calendar for one ticker just
means `earnings_recent = False` / `last_earnings_date = None` for that
ticker, and never crashes the scan.

### What triggers an alert

A Discord alert fires for a ticker only when **both** are true at the same
time: `is_bounce` **and** `earnings_recent`.

### Files

| File | Purpose |
|---|---|
| `screener.py` | Core analysis engine: pulls price/earnings data, computes the bounce + earnings logic per ticker. |
| `alerts.py` | Formats and sends a Discord webhook embed for one matching setup. Logs to console instead of sending if no webhook is configured. |
| `runner.py` | Headless entry point. Reads `data/watchlist.json`, scans it, sends alerts for new matches, updates the "already alerted" log. This is what the scheduled job runs. |
| `app.py` | Streamlit dashboard for interactive browsing. |
| `data/watchlist.json` | **The single source of truth** for the automated scan -- see below. |
| `.github/workflows/daily-scan.yml` | GitHub Actions workflow that runs `runner.py` on a schedule. |

### `data/watchlist.json` -- the real, persistent watchlist

```json
{
  "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO", "NFLX"],
  "proximity_threshold_pct": 2.0,
  "earnings_lookback_days": 7,
  "bounce_lookback_days": 10,
  "alerted": {}
}
```

This file is what `runner.py` (the automated daily scan) always reads. It is
**not** the same as anything typed into the Streamlit dashboard's sidebar --
those changes are session-only, for browsing, and are thrown away when the
browser tab closes. **To permanently change which tickers are monitored, or
the thresholds, you edit this file directly** (see the setup checklist below
for exactly how, with no coding tools required).

The `"alerted"` object is a dedup log, maintained automatically by
`runner.py`: it maps each ticker to the last earnings date it was already
alerted on, so the same earnings event doesn't spam Discord every day it
remains within the lookback window. You don't need to touch this field
yourself.

---

## 2. One-time setup checklist -- from this code to a live, working tool

This section assumes **zero prior experience** with GitHub, Streamlit, or
Discord webhooks. Follow it top to bottom once, and you'll have a live
dashboard plus daily automated Discord alerts.

### Step 1: Create a GitHub account (skip if Alex already has one)

1. Go to github.com in a browser and click **Sign up**.
2. Follow the prompts (email, password, username). It's free.

### Step 2: Push this code to a new GitHub repository

This step is normally done by whoever is comfortable with git/command line
(Mike). In short:
1. On github.com, click the **+** icon (top right) → **New repository**.
2. Give it a name (e.g. `alex-ema-scanner`), leave it **Public** or
   **Private** (either works), don't add a README/gitignore (this project
   already has them), and click **Create repository**.
3. Follow GitHub's "push an existing repository" instructions shown on that
   page to push this local project to it.

### Step 3: Deploy the dashboard on Streamlit Community Cloud

1. Go to **share.streamlit.io**.
2. Click **Sign in** / **Continue with GitHub**, and log in with the GitHub
   account/login from Step 1. (Streamlit Community Cloud is free and uses
   your GitHub login -- there's no separate password to create.)
3. Click **New app** (sometimes labeled **Create app**).
4. Choose the GitHub repository from Step 2, choose the `main` branch, and
   set the "Main file path" to `app.py`.
5. Click **Deploy**. Streamlit will install everything from
   `requirements.txt` automatically and give you a public URL for the
   dashboard (looks like `https://your-app-name.streamlit.app`).

### Step 4: Create a Discord webhook in Alex's Discord server

A "webhook" is just a private URL that lets an outside program (this
scanner) post messages into a specific Discord channel, without needing a
bot or a login.

1. Open Discord and go to the server where alerts should be posted.
2. Right-click the channel you want alerts in (or click the gear icon on it)
   → **Edit Channel**.
3. Click **Integrations** in the left sidebar.
4. Click **Webhooks** → **New Webhook**.
5. (Optional) Give it a name like "EMA Scanner" and click **Copy Webhook
   URL**. Keep this URL private -- anyone who has it can post messages into
   that channel.

### Step 5: Add the webhook URL as a secret in BOTH places

The webhook URL needs to be given to **two** separate systems, because they
run independently: Streamlit runs the dashboard, and GitHub Actions runs the
once-a-day automated scan.

**A) In Streamlit Community Cloud** (so the dashboard could use it too):
1. Go to your app's page on share.streamlit.io.
2. Click the **⋮** (three dots) menu on your app → **Settings** → **Secrets**.
3. Add:
   ```
   DISCORD_WEBHOOK_URL = "paste-the-webhook-url-here"
   ```
4. Click **Save**. The app will restart automatically.

**B) In the GitHub repository** (this is the one that actually matters for
the daily automated alert, since `runner.py` runs there):
1. On github.com, open your repository.
2. Click **Settings** (top of the repo page, not your account settings).
3. In the left sidebar: **Secrets and variables** → **Actions**.
4. Click **New repository secret**.
5. Name: `DISCORD_WEBHOOK_URL`. Value: paste the webhook URL. Click **Add
   secret**.

Once both are set, the daily GitHub Actions workflow
(`.github/workflows/daily-scan.yml`) will run `runner.py` automatically on
weekdays and post real Discord alerts.

> **Note on timing**: the schedule is set to 21:30 UTC, which is
> approximately 4:30-5:30pm US Eastern Time depending on the time of year
> (GitHub Actions' cron schedule doesn't automatically adjust for Daylight
> Saving Time). Being off by an hour twice a year is harmless for this use
> case.

### Step 6: How to change the watchlist or thresholds later (no coding needed)

Alex can do this himself, entirely from the GitHub website, whenever he wants
to add/remove a ticker or tweak a threshold:

1. Go to the repository on github.com.
2. Click on `data` folder, then click `watchlist.json`.
3. Click the pencil (✏️) icon in the top right to edit the file.
4. Edit the tickers list (must be in quotes, comma-separated, like
   `"AAPL", "MSFT"`) and/or the number values for the thresholds.
5. Scroll down and click **Commit changes**.

That's it -- the next scheduled run (or the dashboard's next redeploy) will
pick up the new settings automatically. Do **not** edit the `"alerted"`
section; the scanner manages that automatically to avoid duplicate alerts.

---

## Running it locally (for Mike / development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Dashboard:
streamlit run app.py

# One-off headless scan (uses data/watchlist.json, alerts to Discord or console):
python runner.py
```

Create a local `.env` file (not committed, see `.gitignore`) with:

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

If `DISCORD_WEBHOOK_URL` is unset, alerts are logged to the console instead
of failing.
