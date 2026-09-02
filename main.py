#!/usr/bin/env python3
"""Poll Startup Jobs for new software-engineering internships and email a digest.

Runs standalone (stdlib only) so a scheduled task can drive it without Claude.
"""

import argparse
import datetime as dt
import html
import json
import os
import pathlib
import smtplib
import ssl
import sys
from email.message import EmailMessage

import jobfilter
import sjclient

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
STATE = HERE / "state.json"
SECRETS = HERE / "secrets.json"
LOG = HERE / "poller.log"

# dt.UTC is 3.11+; this keeps the code working on Ubuntu 22.04's Python 3.10.
UTC = dt.timezone.utc

# Each pass is one search_jobs query; results are merged and de-duplicated by id.
# role=engineering is the broad spine; the keyword passes catch listings the
# taxonomy tags differently, plus co-op phrasing, which dominates in Canada.
PASSES = [
    ({"employment_type": "internship", "role": "engineering"}, 3),
    ({"employment_type": "internship", "q": "software"}, 2),
    ({"employment_type": "internship", "q": "developer"}, 1),
    ({"q": "software engineer intern"}, 1),
    # Bare "co-op" beats any longer phrase here: the search matches all terms, so
    # "software engineering co-op" returns nothing while this surfaces the Canadian
    # co-op postings that the internship employment_type misses entirely.
    ({"q": "co-op"}, 2),
]


MAX_LOG_BYTES = 1_000_000


def log(msg):
    line = f"{dt.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    # Scheduled forever, so cap the file and keep one previous generation.
    try:
        if LOG.exists() and LOG.stat().st_size > MAX_LOG_BYTES:
            LOG.replace(LOG.with_suffix(".log.1"))
    except OSError:
        pass
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"WARN: could not read {path.name} ({exc}); using default")
        return default


def get_secret(cfg, key):
    """Env var wins; otherwise fall back to secrets.json."""
    env_name = cfg.get("env_vars", {}).get(key)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()
    # Gmail shows app passwords as "abcd efgh ijkl mnop"; it accepts either form,
    # but strip so a trailing newline from an editor can't fail the login.
    return (load_json(SECRETS, {}).get(key) or "").strip() or None


def fetch(api_key, since):
    seen, merged = set(), []
    for filters, pages in PASSES:
        args = dict(filters)
        if since:
            args["posted_after"] = since
        try:
            jobs = sjclient.search_jobs(api_key=api_key, max_pages=pages, **args)
        except sjclient.SJError as exc:
            log(f"  WARN: pass {filters} failed: {exc}")
            continue
        added = 0
        for job in jobs:
            jid = job.get("id")
            if jid is not None and jid not in seen:
                seen.add(jid)
                merged.append(job)
                added += 1
        log(f"  pass {filters}: {len(jobs)} fetched, {added} new to this run")
    return merged


def company_of(job):
    company = job.get("company")
    if isinstance(company, dict):
        return company.get("name") or ""
    return company or job.get("company_name") or ""


def render(jobs, priority):
    def tier(job):
        code = ((job.get("location") or {}).get("country_code") or "").upper()
        if code in priority:
            return f"{code} ⭐"
        if code:
            return code
        return "REMOTE" if job.get("workplace_type") == "remote" else "--"

    text_lines, rows = [], []
    for job in jobs:
        loc = job.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x) or "-"
        wt = job.get("workplace_type") or ""
        posted = (job.get("published_at") or "")[:10]
        company = company_of(job)
        url = job.get("url") or ""

        text_lines.append(
            f"[{tier(job)}] {job['title']}\n"
            f"    {company} | {where} | {wt} | posted {posted}\n"
            f"    {url}\n"
        )
        rows.append(
            "<tr><td style='padding:10px 12px;border-bottom:1px solid #eee'>"
            f"<a href='{html.escape(url)}' "
            "style='font-weight:600;color:#1a56db;text-decoration:none'>"
            f"{html.escape(job['title'])}</a><br>"
            "<span style='color:#555;font-size:13px'>"
            f"{html.escape(company)} &middot; {html.escape(where)} &middot; "
            f"{html.escape(wt)} &middot; {posted}</span></td>"
            "<td style='padding:10px 12px;border-bottom:1px solid #eee;"
            "white-space:nowrap;font-size:13px;color:#333'>"
            f"{html.escape(tier(job))}</td></tr>"
        )

    text = "\n".join(text_lines) or "No new listings."
    plural = "s" if len(jobs) != 1 else ""
    html_body = (
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;max-width:720px'>"
        f"<h2 style='margin:0 0 4px'>{len(jobs)} new startup SWE internship{plural}</h2>"
        "<p style='color:#666;margin:0 0 16px;font-size:14px'>"
        "⭐ = priority country &middot; source: startup.jobs</p>"
        "<table style='border-collapse:collapse;width:100%'>"
        + "".join(rows) +
        "</table></div>"
    )
    return text, html_body


def send_email(cfg, subject, text, html_body, logger=None):
    # atswatch.py shares this function but keeps its own log file, so let the
    # caller supply its logger instead of writing everything to poller.log.
    log_fn = logger or log
    ec = dict(cfg["email"])
    # Env vars win so the repo can be public: the address lives in a CI secret,
    # never in config.json.
    # Precedence: env var (CI secret) > secrets.json (local, gitignored) > config.json
    ec["to"] = (os.environ.get("ALERT_EMAIL_TO")
                or get_secret(cfg, "alert_email_to")
                or ec.get("to", "")).strip()
    ec["from"] = (os.environ.get("ALERT_EMAIL_FROM")
                  or get_secret(cfg, "alert_email_from")
                  or ec.get("from") or ec["to"]).strip()
    if not ec["to"]:
        log_fn("ERROR: no recipient. Set ALERT_EMAIL_TO or config.json email.to")
        return False
    password = get_secret(cfg, "gmail_app_password")
    if not password:
        log_fn("ERROR: no Gmail app password found (env var or secrets.json). Digest not sent.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ec["from"]
    msg["To"] = ec["to"]
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    # Some hosts (notably Oracle Cloud) filter outbound mail ports. 465 implicit TLS
    # is the primary; 587 STARTTLS is tried if the first is unreachable, so a blocked
    # port degrades to a retry rather than a silent nightly failure.
    ctx = ssl.create_default_context()
    attempts = [("ssl", int(ec.get("smtp_port", 465)))]
    fallback = int(ec.get("smtp_fallback_port", 587))
    if fallback and fallback != attempts[0][1]:
        attempts.append(("starttls", fallback))

    for mode, port in attempts:
        try:
            if mode == "ssl":
                srv = smtplib.SMTP_SSL(ec["smtp_host"], port, context=ctx, timeout=30)
            else:
                srv = smtplib.SMTP(ec["smtp_host"], port, timeout=30)
            with srv:
                if mode == "starttls":
                    srv.starttls(context=ctx)
                srv.login(ec["from"], password)
                srv.send_message(msg)
            log_fn(f"Emailed {ec['to']}: {subject}" + (f" (via {port})" if port != 465 else ""))
            return True
        except smtplib.SMTPAuthenticationError:
            # Bad credentials will fail identically on the other port - stop here.
            log_fn("ERROR: Gmail rejected the login. Use a 16-char App Password, "
                   "not your normal account password.")
            return False
        except (smtplib.SMTPException, OSError) as exc:
            log_fn(f"WARN: send via port {port} failed: {exc}")
    log_fn("ERROR: all SMTP ports failed; digest not sent.")
    return False


def main():
    ap = argparse.ArgumentParser(description="Startup Jobs SWE-intern alerts")
    ap.add_argument("--seed", action="store_true",
                    help="record current listings as already-seen without emailing")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the digest instead of emailing; leaves state untouched")
    args = ap.parse_args()

    cfg = load_json(CONFIG, None)
    if cfg is None:
        log(f"ERROR: {CONFIG.name} is missing.")
        return 1

    state = load_json(STATE, {"seen": {}})
    seen = state.get("seen", {})
    api_key = get_secret(cfg, "startup_jobs_api_key")

    lookback = cfg.get("lookback_days", 14)
    since = (dt.datetime.now(UTC) - dt.timedelta(days=lookback)).strftime("%Y-%m-%d")

    log(f"Polling startup.jobs (since {since}; {len(seen)} ids already seen)")
    raw = fetch(api_key, since)
    log(f"Fetched {len(raw)} unique listings across {len(PASSES)} passes")

    priority = cfg.get("priority_countries", [])
    matched = jobfilter.select(
        raw,
        priority=priority,
        allowed=cfg.get("countries", []),
        include_remote=cfg.get("include_remote_anywhere", True),
    )
    log(f"{len(matched)} match the SWE-internship + location filters")

    fresh = [j for j in matched if str(j["id"]) not in seen]
    log(f"{len(fresh)} are new since the last run")

    now = dt.datetime.now(UTC).isoformat(timespec="seconds")
    sent = False

    if args.seed:
        log("Seed mode: recording listings as seen, no email.")
    elif not fresh:
        log("Nothing new - no email sent.")
    else:
        cap = cfg.get("max_per_email", 25)
        shown = fresh[:cap]
        more = len(fresh) - len(shown)
        text, html_body = render(shown, priority)
        if more:
            text += f"\n...and {more} more not shown.\n"
        plural = "s" if len(fresh) != 1 else ""
        subject = f"{len(fresh)} new startup SWE intern listing{plural}"
        top = (shown[0].get("location") or {}).get("country_code", "")
        if top in priority:
            subject += f" (top: {top})"

        if args.dry_run:
            print("\n=== SUBJECT ===\n" + subject + "\n\n=== BODY ===\n" + text)
            return 0
        sent = send_email(cfg, subject, text, html_body)
        if not sent:
            log("State NOT updated, so these listings will retry on the next run.")
            return 2

    # Commit state only once the digest is safely delivered (or in seed mode),
    # so a mail failure never silently swallows a batch of listings.
    if args.seed or sent or not fresh:
        for job in matched:
            seen.setdefault(str(job["id"]), now)
        cutoff = dt.datetime.now(UTC) - dt.timedelta(days=cfg.get("state_retention_days", 60))
        seen = {k: v for k, v in seen.items() if dt.datetime.fromisoformat(v) >= cutoff}
        # Skip the write when only last_run would change. On a CI runner that
        # commits state back to git, an unconditional write means a commit every
        # run (144/day); this way the file changes only when there is real news.
        if seen != state.get("seen", {}):
            STATE.write_text(json.dumps({"seen": seen, "last_run": now}, indent=1),
                             encoding="utf-8")
            log(f"State saved ({len(seen)} ids retained)")
        else:
            log(f"State unchanged ({len(seen)} ids); not rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
