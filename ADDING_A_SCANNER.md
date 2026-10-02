# Adding a new scanner (no coding tools needed)

Every scanner is one small Python file in the `scanners/` folder. When you add
a file there, a new tab appears on the dashboard and the scheduled GitHub job
starts sending its Discord alerts -- nothing else to wire up.

## Steps (all on the GitHub website)

1. In the repository, open `scanners/_template.py` and click the **Copy raw
   file** button (the two-squares icon above the code).
2. Go back to the `scanners` folder, click **Add file → Create new file**, and
   name it `scanners/<name>.py` -- for example `scanners/volume_spike.py`.
   Use lowercase letters, numbers and underscores only. **The file name is the
   scanner's ID.**
3. Paste the template and change the `name`, `description` and the logic inside
   `run()`.
4. *(Optional)* To change settings without touching the code, click **Add file
   → Create new file** and name it `data/config/<name>.json`, with only the
   settings you want to override:
   ```json
   {
     "watchlist": "momentum",
     "lookback_days": 30
   }
   ```
   Watchlists live in `data/watchlists.json`; add your own list there if needed.
5. Click **Commit changes**. The dashboard picks it up on its next reload, and
   the daily alerts include it from the next scheduled run.

## Turning a scanner off

Create (or edit) `data/config/<name>.json` and set:

```json
{ "enabled": false }
```

The scanner disappears from the dashboard and stops alerting. Set it back to
`true` (or delete the file) to turn it on again.

## JSON typo pitfalls

- Every name and every text value needs **double quotes**: `"watchlist": "momentum"`.
- Put a **comma** after every line except the last one before `}`.
- No comma after the last item. No `//` comments. No single quotes.
- If there's a typo, the dashboard shows a **⚠** tab naming the file and the
  line number, and the scheduled job skips only that scanner and keeps going.
  The same applies to a Python file with a mistake: it shows as a ⚠ tab and
  never breaks the other scanners.
- Never edit `data/alert_log.json` -- it's the "already alerted" memory.

## Asking Claude to write one

You can describe the idea in plain English and ask Claude to write the file.
For example:

> Look at `scanners/_template.py` and `ADDING_A_SCANNER.md` in this repo. Write
> a new scanner file `scanners/volume_spike.py` that flags stocks whose volume
> today is more than 3x their 20-day average volume. Keep the same structure as
> the template and tell me what the file name and any config file should be.

Paste the result into a new file as in the steps above. Keep it to one scanner
per file.
