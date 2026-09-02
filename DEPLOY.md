# Deploying for 24/7 alerts (free)

The Windows tasks only fire while your machine is awake. To get alerts around the
clock, run it on GitHub Actions.

**Use a PUBLIC repo.** Public repos get unlimited free Actions minutes. Private repos
get 2,000 min/month, and a 10-minute schedule burns roughly 2,100+ — you'd run out
mid-month. Nothing sensitive is in the repo: `secrets.json` is gitignored and
`config.json` ships with blank email fields, so your address and password live only in
GitHub Secrets.

## Steps

1. Create a new **public** repo on GitHub — no README or licence, this folder has one.

2. Push:

   ```powershell
   cd C:\Users\Raj\startup-intern-alerts
   git remote add origin https://github.com/<you>/startup-intern-alerts.git
   git push -u origin main
   ```

3. Add secrets under **Settings → Secrets and variables → Actions**:

   | Secret | Value |
   |---|---|
   | `GMAIL_APP_PASSWORD` | your 16-char app password |
   | `ALERT_EMAIL_TO` | the address to alert |
   | `STARTUP_JOBS_API_KEY` | optional; leave unset for the free tier |

4. Open the **Actions** tab, enable workflows, then use **Run workflow** on
   *intern alerts* to confirm it emails you.

5. Turn off the local tasks, or you'll get duplicate alerts — the two copies keep
   separate state and will each report the same postings:

   ```powershell
   Disable-ScheduledTask -TaskName StartupInternAlerts
   Disable-ScheduledTask -TaskName StartupInternAlerts-Realtime
   ```

## What to expect

- **Schedules drift.** GitHub delays scheduled workflows under load, so `*/10`
  realistically fires every 10–25 minutes. Far better than the 24h embargo, but not
  the exact cadence the local task gave you.
- **Cron is UTC**, not local time.
- **60 days of repo inactivity disables schedules.** GitHub emails you first; click
  re-enable, or push any commit to reset the clock.
- The workflow commits `state.json` / `ats_state.json` back. Because those change only
  when new postings appear, that's a few commits a week — not one per run.

## Why the workflow looks the way it does

- `permissions: contents: write` — needed to commit state back.
- `concurrency` — stops the 10-min and 30-min schedules racing to push state.
- The `if` on `github.event.schedule` picks which watcher runs: `7,37 * * * *` runs the
  broad startup.jobs sweep, anything else (including a manual run) runs the real-time
  ATS watcher.
- `git pull --rebase` before push — a second run may have pushed since checkout.
- `[skip ci]` in the commit message — state commits must not retrigger the workflow.

## Verified before shipping

The scripts were run with `secrets.json` moved aside and credentials supplied purely
through environment variables, exactly as CI does it:

```
35 matching postings across 48 boards; 1 new
Emailed <you>: 1 new SWE intern posting (1 in Canada)
```

A staged-file scan also confirmed no email address or password appears in any tracked
file.

## If you want exact timing instead

An **Oracle Cloud Always Free** VM (free forever, no expiry) runs a real 10-minute cron
with no drift — copy the folder over, `pip` nothing, and add two crontab lines:

```cron
*/10 * * * * cd ~/startup-intern-alerts && /usr/bin/python3 atswatch.py
7,37 * * * * cd ~/startup-intern-alerts && /usr/bin/python3 main.py
```

Set `STARTUP_JOBS_GMAIL_APP_PASSWORD` and `ALERT_EMAIL_TO` in the crontab environment,
or keep a local `secrets.json`. It needs a credit card for identity verification, which
GitHub Actions does not.
