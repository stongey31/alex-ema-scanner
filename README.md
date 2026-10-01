# Moving Average Bounce Scanner

A small tool that screens a watchlist of mega-cap stocks for a pullback to a
key moving average that reversed upward, and pings a Discord channel when it
finds one. It checks **four separate signals** per ticker:

| Signal | Alerts when |
|---|---|
| **200 EMA** | Bounce + RSI under 30 during the pullback + earnings report in the last 7 days |
| **200 SMA** | Bounce + RSI under 30 during the pullback |
| **50 EMA** | Bounce + RSI under 30 during the pullback |
| **50 SMA** | Bounce + RSI under 30 during the pullback |

Every alert (and every dashboard row) also shows the current RSI, the lowest
RSI during the pullback, and the **last and next earnings dates**.

There's a Streamlit dashboard (`app.py`) for browsing the scan interactively,
and a headless script (`runner.py`) meant to be run once a day by a scheduled
job (GitHub Actions, see `.github/workflows/daily-scan.yml`) that fires the
actual Discord alerts.

---

## 1. What it does, and exactly what "bounce" means

### The moving averages

For each ticker, the scanner pulls **2 years** of daily price history and
computes four moving averages over the whole series:

```python
ema = hist['Close'].ewm(span=N, adjust=False).mean()   # N = 50 and 200
sma = hist['Close'].rolling(window=N).mean()           # N = 50 and 200
```

Two years rather than one so the 200-day lines are reliable -- a 200-day SMA
has no value at all until there are 200 days of data, and a 200-day EMA needs
a long run-up to settle.

**EMA vs SMA:** the SMA is a plain average of the last N closes. The EMA
weights recent days more heavily, so it reacts faster. They're tracked as
separate signals because price often respects one and not the other.

### The bounce logic (same rule for all four averages)

The scanner looks at the **last 10 trading sessions** (configurable via
`bounce_lookback_days`, includes today) and checks each day's closing price
against *that day's own* value of the moving average:

1. **Touch**: if any of those days closed within **2.0%** (configurable via
   `proximity_threshold_pct`) of the average, the ticker "touched" it. If
   more than one day in the window touched, the day with the **lowest
   close** is used as the reference touch day -- the deepest point of the
   pullback.
2. **Bounce confirmation**: the bounce only counts if, in addition to having
   touched, **today's** close is:
   - **(a)** above today's value of the average, **and**
   - **(b)** higher than the close on that touch day.

Touching the average alone tells you nothing -- price could still be sliding
through it. Requiring today's close to be both above the line *and* above the
touch-day close confirms an actual upward reversal: **a pullback to the
average, followed by a bounce upward.**

This is deliberately **not symmetric**. Price rallying up into an average
from below and getting rejected back down is NOT what this tool looks for.

### The RSI filter

RSI (Relative Strength Index) measures how hard a stock has been bought or
sold recently, on a 0-100 scale. Under 30 is conventionally "oversold." The
scanner uses the standard **14-day RSI with Wilder's smoothing** (the same
calculation TradingView and StockCharts use).

A signal only triggers if RSI dropped **below 30 on any day in the same
10-session window** -- i.e. somewhere during the pullback -- not necessarily
today. By the time a bounce is confirmed, RSI has usually already climbed back
off its low, so requiring RSI under 30 *today* would almost never fire. The
pattern being caught is "oversold during the pullback, then price reversed."

Both the threshold (`rsi_oversold_threshold`, default 30) and the period
(`rsi_period`, default 14) are configurable in `data/watchlist.json`.

**Heads-up:** RSI under 30 is a strict filter. Mega caps often pull back to
their 50- or 200-day lines with RSI only reaching the 30s or 40s, so it can be
normal to go weeks without an alert. The dashboard shows the price-only
bounces (the "Bounce?" columns) separately, so you can see near-misses that
failed only the RSI filter.

### The earnings logic

Earnings is a **required condition only for the 200 EMA signal** (that's the
original post-earnings setup): it needs the most recent **past** earnings
date (from `yfinance`'s `get_earnings_dates()`) to fall within the last **7
days** (configurable via `earnings_lookback_days`).

For the other three signals, earnings is **informational only**: the last
and next earnings dates are shown, but don't affect whether they trigger.

yfinance's earnings-calendar data is known to be inconsistent -- sometimes
timezone-aware, sometimes not, sometimes empty. `screener.py` handles all of
that defensively: a missing or malformed earnings calendar for one ticker
just means no earnings dates for that ticker (and no 200 EMA signal), and
never crashes the scan.

### What triggers an alert

See the table at the top. A ticker that triggers more than one signal on the
same day gets **one** Discord message listing every line it bounced off.

### Files

| File | Purpose |
|---|---|
| `screener.py` | Core analysis engine: pulls price/earnings data, computes the four moving averages, RSI, bounce and earnings logic per ticker. |
| `alerts.py` | Formats and sends a Discord webhook embed for a ticker's triggered signals. Logs to console instead of sending if no webhook is configured. |
| `runner.py` | Headless entry point. Reads `data/watchlist.json`, scans it, sends alerts for new matches, updates the "already alerted" log. This is what the scheduled job runs. |
| `app.py` | Streamlit dashboard for interactive browsing. |
| `data/watchlist.json` | **The single source of truth** for the automated scan -- see below. |
| `.github/workflows/daily-scan.yml` | GitHub Actions workflow that runs `runner.py` on a schedule and saves the "already alerted" log back to the repo. |

### `data/watchlist.json` -- the real, persistent watchlist

```json
{
  "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO", "NFLX"],
  "proximity_threshold_pct": 2.0,
  "earnings_lookback_days": 7,
  "bounce_lookback_days": 10,
  "rsi_period": 14,
  "rsi_oversold_threshold": 30,
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
`runner.py`. It maps each ticker + signal (e.g. `"AAPL:sma50"`) to the touch
date of the bounce it already alerted on. As long as that touch date is still
inside the 10-session window, it's the same pullback and won't alert again,
so one bounce doesn't spam Discord every day. A fresh pullback later alerts
normally. An alert is only logged once Discord actually accepted it, so a
missing or broken webhook never silently swallows an alert. The daily GitHub
job commits this file back to the repo after each run (you'll see commits
titled "Update alert log"). You don't need to touch this field yourself.

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
