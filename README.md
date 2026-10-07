# Alex's Scanners

A small set of stock scanners with a web dashboard and Discord alerts. Each
scanner is one file in the `scanners/` folder, so new ones are easy to add
(see [ADDING_A_SCANNER.md](ADDING_A_SCANNER.md)).

**Two parts:**

- **The dashboard** (`app.py`, hosted on Streamlit Community Cloud): one tab per
  scanner. Pick tickers, change settings for your session, click **Run**. It
  is for browsing only: it never sends Discord messages, and nothing you change
  there is saved.
- **The scheduled alerts** (`runner.py`, run by GitHub Actions): scans on a
  schedule and posts to Discord. It only alerts on **new** hits, never the same
  one twice.

## Scanners

| Scanner | File | Looks for |
|---|---|---|
| **Moving Average Bounce** (daily) | `scanners/ma_bounce.py` | A pullback to the 50/200-day EMA/SMA with RSI under 30, then an upward reversal |
| **Pre-market Momentum** (intraday) | `scanners/premarket_momentum.py` | A gap up of 10%+ before the open on at least 5x the normal volume |

Which tickers each one scans lives in `data/watchlists.json`, and each scanner's
settings in `data/config/<scanner>.json` (see "Editing settings and watchlists
on GitHub" below).

---

## Moving Average Bounce

It checks **four separate signals** per ticker:

| Signal | Alerts when |
|---|---|
| **200 EMA** | Bounce + RSI under 30 during the pullback + earnings report in the last 7 days |
| **200 SMA** | Bounce + RSI under 30 during the pullback |
| **50 EMA** | Bounce + RSI under 30 during the pullback |
| **50 SMA** | Bounce + RSI under 30 during the pullback |

Every alert (and every dashboard row) also shows the current RSI, the lowest
RSI during the pullback, and the **last and next earnings dates**.

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
(`rsi_period`, default 14) are configurable in `data/config/ma_bounce.json`.

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
timezone-aware, sometimes not, sometimes empty. `scanners/ma_bounce.py` handles all of
that defensively: a missing or malformed earnings calendar for one ticker
just means no earnings dates for that ticker (and no 200 EMA signal), and
never crashes the scan.

### What triggers an alert

A ticker that triggers more than one signal on the same day gets **one**
Discord message listing every line it bounced off.

---

## Pre-market momentum scanner

Finds stocks that are **gapping up** before the market opens on **unusually
heavy volume**.

- **Gap %** = (latest price - yesterday's close) / yesterday's close x 100.
  The default alert threshold is **10%** (`min_gap_pct`).
- **RelVol (relative volume)** = the volume traded so far today, divided by the
  *average* volume traded over the *same stretch of the morning* (from 4:00am ET
  up to the same clock time) on each of the previous 10 trading days. A RelVol
  of 5 means "5x the usual volume for this time of day." Default threshold: 5x.
- A hit also needs a price of at least $1 and at least 50,000 shares traded so
  far (to skip junk).

**Data and its limits.** Prices and volume come from Alpaca's market-data API:

- Prices use Alpaca's **IEX** feed (a single exchange - good enough for a
  price).
- Volume uses the **SIP** feed (the whole market) - but on Alpaca's **free
  plan, SIP data is delayed ~15 minutes**, so the scanner counts volume only up
  to 16 minutes ago (`volume_delay_minutes`) and compares it to history up to
  the same time. If the free plan refuses SIP data entirely, the scanner falls
  back to **IEX-only volume**, which is a small slice of the real volume (thin
  and noisy) - the dashboard says so when that happens.
- With Alpaca's **paid plan (about $99/month, "Algo Trader Plus")** the SIP
  data is real-time: set `"volume_delay_minutes": 0` in
  `data/config/premarket_momentum.json`.

### Whole-market mode (the default)

Like DAS Trader's scanner, it doesn't use a fixed list. Each run works in two stages:

1. **Sweep.** It asks Alpaca for every tradable US stock (~12,700 symbols, plain
   tickers only: no warrants/rights/units), then fetches the latest price of all
   of them (about 26 calls, ~10 seconds). It keeps only **gappers**: last trade
   today and within the last 30 minutes, price at least $1, and up **10% or more**
   from the previous close. The biggest 60 gappers move on.
2. **Volume check.** For just those survivors it does the RelVol check described
   above. A stock is flagged only if RelVol >= 5x **and** the gap is >= 10% **and**
   the price/volume floors pass. If the volume-based previous close disagrees with
   the sweep's by more than 20% (usually a stock split) the row says
   "prev close mismatch (split?)" and is never flagged.

The result lists only the stocks that reached stage 2, with a summary such as
*"Swept 12,687 symbols; 14 gapped >=10%; 3 flagged"*.

**Free-plan limits (honest version):**

- Prices come from the **IEX** feed (real time, but one exchange, so thin).
  Volume comes from the **SIP** feed and is **delayed ~15 minutes**.
- Alpaca allows **200 calls per minute**. One sweep is ~26 calls, so don't
  auto-refresh aggressively (auto-refresh is **off** by default; each refresh
  costs ~26 calls).
- **After-hours / very early numbers are noisy:** IEX prints are thin, and stock
  splits can look like 700-3000% "gaps". That's why stage 2 must confirm.
- The Alpaca keys are **paper-trading keys that only read market data**. This
  tool cannot place trades.
- There is no ETF filter (the assets list can't tell ETFs apart reliably).

**Switching back to a fixed list:** set `"universe_mode": "watchlist"` in
`data/config/premarket_momentum.json` (it then scans the `momentum` list in
`data/watchlists.json`). On the dashboard you can also change "Universe" in the
tab's Settings (this session only).

| Setting (`data/config/premarket_momentum.json`) | Default | Meaning |
|---|---|---|
| `universe_mode` | `"whole_market"` | `"whole_market"` or `"watchlist"` |
| `watchlist` | `"momentum"` | List used in watchlist mode |
| `assets_base_url` | `https://paper-api.alpaca.markets` | Alpaca Trading API host that lists tradable stocks |
| `min_gap_pct` | `10.0` | Minimum gap vs. previous close (%) |
| `min_rel_volume` | `5.0` | Minimum RelVol (x) |
| `min_price` | `1.0` | Minimum price, also used in the sweep |
| `min_volume` | `50000` | Minimum shares traded so far |
| `max_stage2_candidates` | `60` | How many top gappers get the volume check |
| `min_trade_age_minutes` | `30` | Sweep ignores symbols whose last trade is older than this |
| `volume_delay_minutes` | `16` | Volume delay (0 on a paid plan) |
| `lookback_days` / `min_history_days` | `10` / `5` | Sessions averaged for RelVol |

### Turning on the pre-market scanner

Until you do this, the dashboard tab says "Alpaca API keys are not set up yet"
(with a clearly labelled **FAKE data** demo) and the scheduled job does nothing.

1. Go to **alpaca.markets** and create a free account. (You do **not** need to
   fund it or trade; the free plan's **Market Data "Basic"** is enough to start.)
2. In the Alpaca dashboard, generate an **API key** and **secret**. (Keys from a
   paper-trading account work fine for market data, and can't trade for you here.) Copy both somewhere safe -
   the secret is only shown once.
3. Add the keys in **both** places (same idea as the Discord webhook below):
   - **Streamlit**: your app on share.streamlit.io -> **⋮** -> **Settings** ->
     **Secrets**, add:
     ```
     ALPACA_API_KEY = "paste-the-key-here"
     ALPACA_API_SECRET = "paste-the-secret-here"
     ```
     and **Save**.
   - **GitHub**: repository -> **Settings** -> **Secrets and variables** ->
     **Actions** -> **New repository secret**. Add one named `ALPACA_API_KEY` and
     another named `ALPACA_API_SECRET`.

### When the scheduled alerts run - honest limits

- **Daily scan:** weekdays at about 4:30-5:30pm ET (see the timing note below).
- **Pre-market scan:** weekdays, two schedules (12:00 and 13:00 UTC) so one of
  them lands around 8:00am ET in both summer and winter time; it only acts if
  it is between 7:45 and 9:29am ET.
- GitHub's schedules are **best-effort**: runs are often **delayed** by several
  minutes (sometimes much more). This is **not a real-time, minute-by-minute
  scanner** - think "a look at the pre-market once or twice a morning."
- The dashboard can **auto-refresh every minute, but only while the page is open**
  (toggle "Auto-refresh while this page is open"; it's off by default).
- True always-on alerting (a server watching the market all morning) is out of
  scope for this setup.
- **GitHub switches off scheduled workflows after 60 days with no activity in
  the repo.** If alerts stop, open the repository's **Actions** tab, click the
  workflow in the left list, and press **Enable workflow**. (Any commit, such as
  the automatic "Update alert log" ones, also counts as activity.)

---

### Files

| File | Purpose |
|---|---|
| `app.py` | The dashboard (one tab per scanner). |
| `runner.py` | Headless entry point the scheduled jobs run (`--lane daily` or `--lane intraday`). |
| `scanners/` | One file per scanner. `scanners/_template.py` is the starting point for a new one. |
| `core/` | Shared plumbing (settings, alert memory, Discord, Alpaca, charts). You shouldn't need to touch it. |
| `data/watchlists.json` | Named lists of tickers (`mega_caps`, `momentum`, ...). |
| `data/config/<scanner>.json` | Settings for one scanner (optional; defaults live in the scanner file). |
| `data/alert_log.json` | The "already alerted" memory, maintained automatically. **Never edit it.** |
| `.github/workflows/` | The scheduled jobs (`daily-scan.yml`, `premarket-scan.yml`). |

### Editing settings and watchlists on GitHub

No coding tools needed. On github.com:

1. Open the repository -> the `data` folder -> `watchlists.json` (to change
   tickers) or `config` -> `ma_bounce.json` / `premarket_momentum.json` (to change
   thresholds).
2. Click the pencil (✏️) icon, edit, and click **Commit changes**.
3. Tickers must be in double quotes and comma-separated, like `"AAPL", "MSFT"`.
   A typo shows up as a ⚠ tab on the dashboard naming the file and line.
4. To turn a scanner off, set `"enabled": false` in its config file.

The scheduled jobs and the dashboard pick up changes on their next run/reload.
**Do not edit `data/alert_log.json`** - the scanners manage it. The Moving
Average Bounce alert log works like this: for each ticker + signal (e.g.
`"AAPL:sma50"`) it remembers the touch date of the bounce already alerted. While
that touch date is still inside the 10-session window it's the same pullback and
won't alert again; a fresh pullback later alerts normally. An alert is only
logged once Discord actually accepted it, so a missing or broken webhook never
silently swallows an alert. The pre-market scanner alerts at most once per
ticker per day.

---

## One-time setup checklist -- from this code to a live, working tool

This section assumes **zero prior experience** with GitHub, Streamlit, or
Discord webhooks. Follow it top to bottom once, and you'll have a live
dashboard plus automated Discord alerts.

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
5. Click **Advanced settings** and choose **Python 3.12**.
6. Click **Deploy**. Streamlit will install everything from
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
(`.github/workflows/daily-scan.yml`) will run `runner.py --lane daily`
automatically on weekdays and post real Discord alerts.

> **Note on timing**: the schedule is set to 21:30 UTC, which is
> approximately 4:30-5:30pm US Eastern Time depending on the time of year
> (GitHub Actions' cron schedule doesn't automatically adjust for Daylight
> Saving Time). Being off by an hour twice a year is harmless for this use
> case.


## Developing locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

streamlit run app.py              # dashboard
python runner.py --list           # list scanners
python runner.py --lane daily --dry-run   # scan, but no Discord / no log writes
pytest -q                         # offline tests (add -m live for the network check)
```

Create a local `.env` file (not committed, see `.gitignore`) with any of:

```
DISCORD_WEBHOOK_URL=...
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
```

If `DISCORD_WEBHOOK_URL` is unset, alerts are logged to the console instead of
failing. `requirements.txt` pins exact versions (except `yfinance`, which only
has a minimum so Yahoo-compatibility fixes get picked up). The app targets
Python 3.12.

## Early Runner Setup scanner (`scanners/early_runner.py`)

Whole-market scan, once a day after the close, for small, low-float stocks breaking out on heavy volume
(the profile of stocks that went on big runs). A research list, not a buy signal: most flags will fizzle.

1. **Sweep** (Alpaca snapshots): closed up at least 3% and costs at least $1.
2. **Volume and trend** (daily bars): volume at least 5x the prior-30-day average, close above the
   highest high of the prior 60 sessions, and above the 10- and 20-day EMA.
3. **Size** (yfinance): market cap under $500M AND float under 25M shares. Short interest is shown as a bonus.
   If yfinance has no data, the stock shows as "unknown" in the table but is not flagged and sends no alert.

Change any number in `data/config/early_runner.json`, for example `{"min_rel_volume": 8, "max_float_m": 15}`.
Needs `ALPACA_API_KEY` / `ALPACA_API_SECRET` as **GitHub Actions secrets** too (the daily workflow passes them);
without them this scanner reports "not configured" and the others keep running. Limits: the free Alpaca plan only
reports IEX volume (volume *ratios* are meaningful, absolute volume is not); float and market cap come from
yfinance (free, unofficial, sometimes missing); revenue growth and catalysts are not scanned.
