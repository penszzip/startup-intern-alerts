#!/usr/bin/env python3
"""Real-time SWE-intern watcher: polls company ATS boards directly.

startup.jobs embargoes new listings for 24h on the free tier, so it can never be
fast. Greenhouse / Lever / Ashby publish the same postings the moment they go
live, unauthenticated. This polls a watchlist of those boards and emails within
minutes of a matching role appearing.

Usage:
  python atswatch.py            poll and email anything new
  python atswatch.py --seed     record current postings as seen, send nothing
  python atswatch.py --dry-run  print matches, change nothing
"""

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

import jobfilter
import main as sjmain

HERE = pathlib.Path(__file__).resolve().parent
WATCHLIST = HERE / "watchlist.json"
STATE = HERE / "ats_state.json"
LOG = HERE / "atswatch.log"

# dt.UTC is 3.11+; this keeps the code working on Ubuntu 22.04's Python 3.10.
UTC = dt.timezone.utc

UA = {"User-Agent": "Mozilla/5.0 (startup-intern-alerts watcher)"}
TIMEOUT = 15

CA_HINT = re.compile(
    r"(?i)\b(canada|canadian|toronto|ottawa|montr[eé]al|vancouver|waterloo|kitchener"
    r"|calgary|edmonton|winnipeg|halifax|victoria|mississauga|markham|burnaby"
    r"|quebec|ontario|\bon\b|\bbc\b|\bqc\b|\bab\b|\bns\b|\bmb\b)"
)
US_HINT = re.compile(
    r"(?i)\b(united states|usa|u\.s\.|new york|san francisco|seattle|boston|austin"
    r"|chicago|los angeles|denver|atlanta|remote - us|palo alto|mountain view"
    r"|sunnyvale|bellevue|nyc|\bca\b|\bny\b|\bwa\b|\bma\b|\btx\b|\bil\b)"
)
REMOTE_HINT = re.compile(r"(?i)\bremote\b|\banywhere\b|\bdistributed\b")


MAX_LOG_BYTES = 1_000_000


def log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    # Runs every 10 min forever, so cap the file and keep one previous generation.
    try:
        if LOG.exists() and LOG.stat().st_size > MAX_LOG_BYTES:
            LOG.replace(LOG.with_suffix(".log.1"))
    except OSError:
        pass
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fetch_json(url: str):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError, OSError) as exc:
        return {"__error__": str(exc)[:120]}


def _iso(value) -> str:
    """Normalise the three ATSes' timestamp formats to an ISO date string."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):          # Lever: epoch milliseconds
        return dt.datetime.fromtimestamp(value / 1000, UTC).isoformat(timespec="seconds")
    return str(value)


def normalise(company: dict, raw) -> list[dict]:
    """Flatten a board response into the shape jobfilter expects."""
    ats, name = company["ats"], company["name"]
    out = []

    if ats == "greenhouse":
        for j in (raw.get("jobs") or []):
            out.append({
                "id": f"gh:{company['slug']}:{j.get('id')}",
                "title": j.get("title") or "",
                "url": j.get("absolute_url") or "",
                "location_text": ((j.get("location") or {}).get("name")) or "",
                "posted_at": _iso(j.get("first_published") or j.get("updated_at")),
            })
    elif ats == "lever":
        for j in (raw if isinstance(raw, list) else []):
            cat = j.get("categories") or {}
            out.append({
                "id": f"lv:{company['slug']}:{j.get('id')}",
                "title": j.get("text") or "",
                "url": j.get("hostedUrl") or "",
                "location_text": cat.get("location") or "",
                "posted_at": _iso(j.get("createdAt")),
            })
    elif ats == "ashby":
        for j in (raw.get("jobs") or []):
            if j.get("isListed") is False:
                continue
            # secondaryLocations are objects, not strings - pull out the names only.
            extra = [s.get("location", "") for s in (j.get("secondaryLocations") or [])
                     if isinstance(s, dict)]
            places = [p for p in [j.get("location") or ""] + extra if p]
            out.append({
                "id": f"as:{company['slug']}:{j.get('id')}",
                "title": j.get("title") or "",
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "location_text": ", ".join(dict.fromkeys(places)),
                "posted_at": _iso(j.get("publishedAt")),
            })

    for job in out:
        job["company"] = name
        job["title"] = jobfilter._mojibake(job["title"])
    return out


def region(job: dict) -> str | None:
    """CA / US / REMOTE, or None when out of scope."""
    text = job.get("location_text") or ""
    if CA_HINT.search(text):
        return "CA"
    if US_HINT.search(text):
        return "US"
    if REMOTE_HINT.search(text):
        return "REMOTE"
    return None


# Non-technical intern tracks. Every company on the watchlist is hand-picked and
# builds software, so an internship there is technical unless the title names one
# of these functions. That lets the watcher keep "Research Internship" at Cohere or
# "Applied Research Intern" at Block, which a keyword allowlist would silently drop.
NON_TECH = re.compile(
    r"""(?ix)
    \b design (er)? \b | \bux\b | \bui\b | user \s* research
    | marketing | \bsales\b | \bgtm\b
    # Samsara posts "Account Development Representative Intern" - a sales track,
    # so match the whole account-* family and the SDR/BDR rep titles, not just
    # account executive/manager.
    | \baccount\b | representative | \bsdr\b | \bbdr\b | quota
    | finance | accounting | \btax\b | \baudit\b | treasury
    | legal | counsel | compliance | policy
    | recruit | talent | people \s* (ops|team) | \bhr\b | human \s* resources
    # Roles that STAFF an internship programme rather than being one. Cohere's
    # "Early Careers & Interns Specialist" is a recruiting job that INTERNISH
    # matches on the word "Interns". Deliberately narrow - bare "early career"
    # must still pass, since "Early Career Software Engineer" is a role we want.
    | interns? \s* (specialist|coordinator|partner|programme?\s*manager)
    | early \s* careers? \s* (specialist|coordinator|partner|programme?|manager|recruit)
    | university \s* relations | campus \s* (recruit|program)
    | communications | \bcomms\b | content | brand | social \s* media | editorial
    | customer \s* (support|success|experience) | community
    | product \s* manage | program \s* manage | project \s* manage
    | business \s* develop | \bstrategy\b | \boperations\b | partnerships
    | procurement | supply \s* chain | logistics | facilities | workplace
    | biolog | chemist | clinical | nursing | \bmedical\b
    """
)


def matches(job: dict) -> bool:
    """Permissive: keep any internship that isn't clearly a non-technical track.

    Deliberately looser than jobfilter.is_software_intern, which polices the whole
    open web via startup.jobs and must be strict. Here the watchlist already does
    the filtering, so recall matters more than precision.
    """
    title = job["title"]
    if not jobfilter.INTERNISH.search(title):
        return False
    if NON_TECH.search(title):
        return False
    # Still drop other engineering disciplines unless the title says software.
    if jobfilter.EXCLUDE.search(title) and not jobfilter.SOFTWARE.search(title):
        return False
    return True


def render(jobs: list[dict]) -> tuple[str, str]:
    order = {"CA": 0, "US": 1, "REMOTE": 2}
    jobs = sorted(jobs, key=lambda j: (order.get(j["_region"], 3), j["company"]))
    lines, rows = [], []
    for j in jobs:
        star = " *" if j["_region"] == "CA" else ""
        lines.append(
            f"[{j['_region']}{star}] {j['title']}\n"
            f"    {j['company']} | {j['location_text']} | posted {j['posted_at'][:10]}\n"
            f"    {j['url']}\n"
        )
        rows.append(
            "<tr><td style='padding:10px 12px;border-bottom:1px solid #eee'>"
            f"<a href='{html.escape(j['url'])}' "
            "style='font-weight:600;color:#1a56db;text-decoration:none'>"
            f"{html.escape(j['title'])}</a><br>"
            "<span style='color:#555;font-size:13px'>"
            f"{html.escape(j['company'])} &middot; {html.escape(j['location_text'])}"
            "</span></td>"
            "<td style='padding:10px 12px;border-bottom:1px solid #eee;"
            "white-space:nowrap;font-size:13px'>"
            f"{html.escape(j['_region'])}{'&nbsp;&#11088;' if j['_region']=='CA' else ''}"
            "</td></tr>"
        )
    plural = "s" if len(jobs) != 1 else ""
    body = (
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;max-width:720px'>"
        f"<h2 style='margin:0 0 4px'>{len(jobs)} new SWE intern posting{plural}</h2>"
        "<p style='color:#666;margin:0 0 16px;font-size:14px'>"
        "Straight from the company job board &mdash; live, no 24h delay.</p>"
        "<table style='border-collapse:collapse;width:100%'>" + "".join(rows) +
        "</table></div>"
    )
    return "\n".join(lines) or "No new postings.", body


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-time ATS intern watcher")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = sjmain.load_json(sjmain.CONFIG, {})
    wl = sjmain.load_json(WATCHLIST, {}).get("companies", [])
    if not wl:
        log("ERROR: watchlist.json has no companies.")
        return 1

    state = sjmain.load_json(STATE, {"seen": {}})
    seen = state.get("seen", {})

    found, quiet = [], 0
    for company in wl:
        tmpl = {
            "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
            "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        }.get(company["ats"])
        raw = fetch_json(tmpl.format(slug=company["slug"]))
        if isinstance(raw, dict) and "__error__" in raw:
            log(f"  WARN {company['name']}: {raw['__error__']}")
            continue
        jobs = normalise(company, raw)
        hits = []
        for job in jobs:
            if not matches(job):
                continue
            reg = region(job)
            if reg is None:
                continue
            job["_region"] = reg
            hits.append(job)
        found.extend(hits)
        # An idle run is the common case (144/day). Log a line per company only
        # when it has matches; otherwise just count it toward the summary.
        if hits:
            log(f"  {company['name']:16} {len(jobs):4} postings, {len(hits)} intern match(es)")
        else:
            quiet += 1
        time.sleep(0.4)

    if quiet:
        log(f"  {quiet} other board(s) polled, no intern matches")

    fresh = [j for j in found if j["id"] not in seen]
    log(f"{len(found)} matching postings across {len(wl)} boards; {len(fresh)} new")

    now = dt.datetime.now(UTC).isoformat(timespec="seconds")
    sent = False

    if args.dry_run:
        text, _ = render(found)
        print("\n=== ALL CURRENT MATCHES ===\n" + text)
        return 0
    if args.seed:
        log("Seed mode: recording as seen, no email.")
    elif not fresh:
        log("Nothing new.")
    else:
        text, body = render(fresh)
        ca = sum(1 for j in fresh if j["_region"] == "CA")
        subject = f"{len(fresh)} new SWE intern posting{'s' if len(fresh) != 1 else ''}"
        if ca:
            subject += f" ({ca} in Canada)"
        sent = sjmain.send_email(cfg, subject, text, body, logger=log)
        if not sent:
            log("State NOT updated; will retry next run.")
            return 2

    if args.seed or sent or not fresh:
        before = dict(seen)
        for job in found:
            seen.setdefault(job["id"], now)
        # Only touch the file when seen[] actually changed - see main.py.
        if seen != before:
            STATE.write_text(json.dumps({"seen": seen, "last_run": now}, indent=1),
                             encoding="utf-8")
            log(f"State saved ({len(seen)} ids)")
        else:
            log(f"State unchanged ({len(seen)} ids); not rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
