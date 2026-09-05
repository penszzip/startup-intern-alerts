#!/usr/bin/env python3
"""Find a company's public ATS job board by trying likely slugs.

Greenhouse, Lever and Ashby all expose unauthenticated JSON job boards keyed by a
company slug. The slug is usually a squashed version of the company name, so try
the obvious variants against all three and report whichever answers.

Usage:  python atsresolve.py "Cohere" "Wealthsimple" ...
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 12
UA = {"User-Agent": "Mozilla/5.0 (startup-intern-alerts ATS resolver)"}

BOARDS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", "jobs"),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", None),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", "jobs"),
}


def slug_variants(name: str) -> list[str]:
    base = name.strip().lower()
    base = re.sub(r"\b(inc|ltd|llc|corp|technologies|technology|labs|co)\b\.?", "", base)
    # Keep a dotted variant BEFORE stripping punctuation: Super.com's Ashby slug is
    # literally "super.com", so squashing it to "supercom" finds nothing.
    dotted = re.sub(r"[^a-z0-9.\s-]", "", base).strip()
    dotted = re.sub(r"[\s-]+", "-", dotted)

    base = re.sub(r"[^a-z0-9\s-]", "", base).strip()
    squashed = re.sub(r"[\s-]+", "", base)
    hyphened = re.sub(r"[\s-]+", "-", base)
    out = [squashed, hyphened]
    if "." in dotted:
        out.append(dotted)
    if squashed.endswith("ai") and len(squashed) > 3:      # cohereai -> cohere
        out.append(squashed[:-2])
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def _fetch(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError, OSError):
        return None


#

# Some companies split hiring across regional boards that share no job ids.
# DoorDash is the clearest case: `doordashusa` carries 465 postings and *zero*
# Canadian locations, while `doordashcanada` holds the entire Toronto org. Miss the
# suffix and you have no visibility into that office at all.
REGION_SUFFIXES = ["canada", "ca", "usa", "us", "international", "global"]


def _board(slug: str) -> tuple[str, int] | None:
    for ats, (tmpl, key) in BOARDS.items():
        data = _fetch(tmpl.format(slug=slug))
        time.sleep(0.2)
        if data is None:
            continue
        jobs = data.get(key) if key else data
        if isinstance(jobs, list) and jobs:
            return ats, len(jobs)
    return None


def resolve(name: str) -> dict | None:
    """Return the first ATS board that exists, plus any regional sibling boards."""
    for slug in slug_variants(name):
        primary = _board(slug)

        # Probe suffixes even when the bare slug has no board — "doordash" 404s
        # while "doordashusa" and "doordashcanada" both exist.
        regions = []
        for suffix in REGION_SUFFIXES:
            if slug.endswith(suffix):
                continue
            sib = _board(slug + suffix)
            if sib:
                regions.append({"name": f"{name} {suffix.title()}", "ats": sib[0],
                                "slug": slug + suffix, "job_count": sib[1]})

        if not primary and not regions:
            continue
        if primary:
            result = {"name": name, "ats": primary[0], "slug": slug,
                      "job_count": primary[1]}
        else:
            head = regions.pop(0)          # no base board; promote a regional one
            result = dict(head)
        if regions:
            result["regional_boards"] = regions
        return result
    return None


def main() -> int:
    names = sys.argv[1:]
    if not names:
        print(__doc__)
        return 1
    found, missing = [], []
    for name in names:
        hit = resolve(name)
        if hit:
            regions = hit.pop("regional_boards", [])
            found.append(hit)
            print(f"  OK    {name:26} {hit['ats']:11} {hit['slug']:22} ({hit['job_count']} jobs)")
            for r in regions:
                found.append(r)
                print(f"    +regional               {r['ats']:11} {r['slug']:22} ({r['job_count']} jobs)")
        else:
            missing.append(name)
            print(f"  --    {name:26} no public board found")
    print(f"\n{len(found)} resolved, {len(missing)} not found")
    if missing:
        print("Not found (may use Workday/Bamboo, or a non-obvious slug):")
        print("  " + ", ".join(missing))
    print("\n" + json.dumps(found, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
