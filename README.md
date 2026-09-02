# Startup Jobs — SWE Intern Alerts

Emails you software-engineering internships / co-ops as they open. **Canada first, then
US, plus remote-anywhere.**

Two watchers: a broad sweep of [startup.jobs](https://startup.jobs) via its MCP server
(wide, but 24h behind), and a real-time watcher polling company ATS boards directly
(fast, but only for companies you list). See *Two watchers, and why*.

Pure Python stdlib — no `pip install`, and it runs without Claude.

**Running 24/7:** the Windows tasks only fire while this machine is awake. See [DEPLOY.md](DEPLOY.md) to run it free on GitHub Actions instead.

## One remaining setup step

The poller sends mail through Gmail SMTP, which needs a 16-character **App Password**
(your normal Google password will not work, and Google blocks it by design):

1. Enable 2-Step Verification: https://myaccount.google.com/signinoptions/two-step-verification
2. Create an app password: https://myaccount.google.com/apppasswords
   (name it e.g. "intern alerts")
3. Save it:

```powershell
cd C:\Users\Raj\startup-intern-alerts
copy secrets.example.json secrets.json
notepad secrets.json     # paste the 16-char password into gmail_app_password
```

Then confirm it works:

```powershell
python main.py
```

Until this is done, runs exit with code 2, log the error, and **leave state untouched** —
so nothing is lost; the next run re-reports the same listings.

## Usage

| Command | Effect |
|---|---|
| `python main.py` | Broad startup.jobs sweep (24h-delayed data) |
| `python main.py --dry-run` | Print the digest, send nothing, leave state alone |
| `python main.py --seed` | Mark everything currently open as "seen" without emailing |
| `python atswatch.py` | Real-time sweep of watchlist company boards |
| `python atswatch.py --dry-run` | Show all current matches on those boards |
| `python atsresolve.py "Name"` | Find a company's ATS board to add to the watchlist |

Run `--seed` if you only want alerts for genuinely *new* postings and don't want the
current backlog of ~58 in your first email.

## Two watchers, and why

**startup.jobs embargoes new listings for 24 hours on the free tier.** Measured
2026-09-02: of the 250 newest listings, 249 were 24–30h old and exactly one was
younger. That delay is the Startup Jobs Pro paywall — Pro advertises a "24-hour head
start" and instant alerts. No amount of polling can beat it.

So there are two watchers:

| Task | Script | Every | Latency | Coverage |
|---|---|---|---|---|
| `StartupInternAlerts` | `main.py` | 30 min | **~24h** (embargo) | Every startup on startup.jobs |
| `StartupInternAlerts-Realtime` | `atswatch.py` | 10 min | **~10 min** | Only companies in `watchlist.json` (48 boards) |

`atswatch.py` skips startup.jobs entirely and polls company ATS boards
(Greenhouse / Lever / Ashby) directly. Those are public, unauthenticated, and
publish the instant a role goes live — verified against listings updated 1.1h earlier.
This is the closest thing to a webhook available; neither the MCP server nor the ATS
APIs offer push subscriptions.

The tradeoff: real-time coverage is **only as wide as your watchlist**. A Toronto
startup not in `watchlist.json` still reaches you on the 24h path.

### Growing the watchlist — this is the main lever

```powershell
python atsresolve.py "Ada" "Clio" "Vector Institute" "Ramp"
```

It tries slug variants against all three ATSes and prints JSON to paste into
`watchlist.json`. Roughly half of companies resolve; the rest use Workday or
BambooHR, which have no clean public board API. Adding 50–100 companies is cheap
(48 boards currently take ~30s per cycle) and directly widens real-time coverage.

**Regional sub-boards.** Some companies split hiring across boards that share no job
ids, and the resolver now probes `canada` / `ca` / `usa` / `us` / `international` /
`global` suffixes for each candidate:

```
OK    Doordash          greenhouse  doordashcanada         (39 jobs)
  +regional             greenhouse  doordashusa            (465 jobs)
  +regional             greenhouse  doordashinternational  (18 jobs)
```

DoorDash is the case that motivated this: `doordashusa` has 465 postings and **zero
Canadian locations**, while the entire Toronto engineering org lives on
`doordashcanada`. Watching only the US board gives no visibility into that office.
It also fixes a resolver blind spot — bare `doordash` 404s, so the old code reported
"no public board found" and gave up before trying any suffix.

Of the current watchlist, DoorDash is the *only* company with a regional split, so
don't assume it's common — but it's cheap to check and expensive to miss.

## Schedule

```powershell
Get-ScheduledTaskInfo -TaskName StartupInternAlerts-Realtime   # last/next run
Start-ScheduledTask    -TaskName StartupInternAlerts-Realtime  # run now
Disable-ScheduledTask  -TaskName StartupInternAlerts-Realtime  # pause
```

Same commands work for `StartupInternAlerts`. Missed runs (laptop asleep) fire on
next wake — `StartWhenAvailable` is set. `MultipleInstances IgnoreNew` stops a slow
run from stacking on the next trigger.

State is kept separately per watcher (`state.json` / `ats_state.json`), so the two
never suppress each other's alerts.

### What an idle run does

When nothing new is found the watcher sends **no email** — that's the common case,
144 times a day. It is not a complete no-op though:

- `ats_state.json` is rewritten, but only `last_run` changes; `seen[]` is untouched
- ~14 lines go to `atswatch.log`

Both logs rotate at 1 MB, keeping one `.log.1` generation, so disk use is capped at
~2 MB per watcher. Per-company lines are only written for boards that actually have
matches; the rest collapse into one "N other board(s) polled" line. Without that,
an idle run wrote 28 lines and the log would have grown ~107 MB/year.

**`ats_state.json` is never pruned, deliberately.** `main.py` expires ids after
`state_retention_days` because startup.jobs only serves a 14-day window, so an
expired id can't come back. ATS boards keep postings open indefinitely (there's a
2023 req on Databricks' board), so pruning there would make a long-open role look
new again and re-alert you. Growth is ~40 bytes per posting — negligible.

## Tuning

`config.json`:

| Key | Meaning |
|---|---|
| `priority_countries` | Sorted to the top of every digest, marked ⭐. Currently `["CA"]` |
| `countries` | Also included, below priority. Currently `["US"]` |
| `include_remote_anywhere` | Keep remote listings from any country |
| `lookback_days` | How far back to query (free tier caps at 14) |
| `max_per_email` | Listings per digest (default 25) |
| `state_retention_days` | How long a job id counts as "seen" (default 60) |

`jobfilter.py` holds the title regexes:

- `SOFTWARE` — what counts as a software role
- `EXCLUDE` — mechanical / electrical / RF / civil etc., dropped unless the title also
  says "software"
- `INTERNISH` — intern / co-op / trainee / apprentice / new grad, incl. plurals

### The two watchers filter differently, on purpose

| | startup.jobs sweep | ATS watcher |
|---|---|---|
| Policy | **Allowlist** — title must match `SOFTWARE` | **Denylist** — keep unless title matches `NON_TECH` |
| Why | Searching the whole open web; without an allowlist every mechanical and nursing intern floods in | Watchlist companies all build software, so an internship there is technical unless it says otherwise |

The denylist is what keeps *"Research Internship (Winter 2027)"* at Cohere and
*"Applied Research Intern"* at Block — real ML roles that name no software keyword and
that an allowlist silently drops. On the ATS side this took matches from 3 to 11.
`NON_TECH` in `atswatch.py` is the list to edit if design/PM/marketing interns start
leaking through. It already covers the sales-track trap: Samsara posts "Account
Development Representative **Intern**", so `NON_TECH` matches the whole `account`
family plus `representative`/`sdr`/`bdr`, not just "account executive/manager".

**Watch the `\b` placement in `INTERNISH`.** It must sit *after* the optional plural:
`intern(ship)?s?\b`, not `intern(ship)?\b`. The latter silently fails on
"Internship**s**" and "New Grad**uate**" — it was dropping a live Wealthsimple Toronto
SWE internship. `\bintern...\b` still correctly rejects "Internal" and "International".

Loosen `EXCLUDE` if you want generically-titled postings like *"Summer 2027 Internship —
Engineering"*, which are currently dropped as ambiguous (they'd drag in the mechanical
ones too).

## How it works

`main.py` runs 5 `search_jobs` queries against `https://api.startup.jobs/mcp` and merges
them by job id. One query on `employment_type=internship, role=engineering` is the spine;
the rest catch listings the taxonomy tags differently. Notably, a bare `q="co-op"` query
surfaces 12+ Canadian co-ops that the `internship` employment type misses entirely —
longer phrases like `"software engineering co-op"` return zero, because the search
requires all terms to match.

Results are then filtered by title, scoped by country, de-duplicated against
`state.json`, and collapsed so one employer re-posting the same role four times under
different ids only appears once.

State is written **only after** the email is confirmed sent, so a mail failure can never
silently swallow a batch of listings.

## Notes

- Free tier: 20 req/min, last 14 days of listings. A run uses ~10 requests.
  A free API key at https://startup.jobs/account/api_keys raises this to 300/min and
  unlocks the full archive — drop it into `secrets.json` as `startup_jobs_api_key`.
- Job titles from the API sometimes arrive as cp1252 mojibake (`Intern â€“ Summer`);
  `jobfilter._mojibake` repairs these while leaving correctly-encoded accents
  (`Montréal`, `Zürich`) untouched.
- `secrets.json`, state files and logs are gitignored.
- The ATS boards are public and unauthenticated. The watcher makes 48 requests per
  10-minute cycle with a 0.4s gap; keep it polite if you grow the watchlist a lot.
