"""Decide which Startup Jobs listings are software-engineering internships."""

import re

# Titles that clearly denote a software discipline. These win over EXCLUDE:
# "Software Engineering Intern (Manufacturing Systems)" is still a SWE role.
SOFTWARE = re.compile(
    r"""(?ix)
    software | \bswe\b | \bsde\b | back[\s-]?end | front[\s-]?end | full[\s-]?stack
    | web \s* (dev|engineer) | mobile \s* (dev|engineer) | \bios\b | android
    | machine \s* learning | deep \s* learning | computer \s* vision | \bnlp\b
    | \bai\b | \bml\b | data \s* (scien|engineer) | analytics \s* engineer
    | devops | site \s* reliability | \bsre\b | platform \s* engineer
    | infrastructure \s* engineer | cloud \s* engineer | security \s* engineer
    | compiler | distributed \s* systems | embedded \s* software | firmware
    | game \s* (dev|programm) | application \s* (dev|engineer) | programmer
    | developer | \bqa\b \s* (engineer|automation) | test \s* automation
    | robotics \s* software | \bapi\b | database | blockchain
    """
)

# Non-software disciplines that also match employment_type=internship + role=engineering.
EXCLUDE = re.compile(
    r"""(?ix)
    mechanical | civil | biomedical | chemical | industrial | structural
    | manufacturing \s* engineer | petroleum | environmental \s* engineer
    | geotech | \bhvac\b | process \s* engineer | packaging | materials \s* engineer
    | electrical \s* engineer | hardware \s* engineer | \brf\b \s* engineer
    | optical | validation \s* engineer | field \s* engineer | sales \s* engineer
    | solutions \s* engineer | pipeline \s* engineer | supplier \s* engineer
    | aerospace \s* structures | thermal | propulsion | avionics \s* hardware
    """
)

# Confirms the listing is genuinely an internship / co-op (not a senior role).
# The \b must sit AFTER the optional plural, or "Internships" and "New Graduate"
# both fail to match. \bintern...\b still correctly rejects "Internal"/"International".
INTERNISH = re.compile(
    r"""(?ix)
    \b intern (ship)? s? \b
    | \b co[\s-]?ops? \b
    | \b trainees? \b
    | \b apprentice (ship)? s? \b
    | \b new \s+ grad (uate)? s? \b
    """
)

# Role slugs from the taxonomy that independently signal software work.
SOFTWARE_SLUG = re.compile(
    r"(?i) software|developer|backend|frontend|full-stack|devops|data-engineer"
    r"|machine-learning|ai-engineer|mobile|web|qa|sre|platform|cloud|security-engineer"
)


def _mojibake(text: str) -> str:
    """Repair UTF-8 bytes that upstream decoded as latin-1 (e.g. 'â€“' -> '–')."""
    if not text or not any(m in text for m in ("â", "Ã", "Â")):
        return text
    # cp1252 first: the classic mojibake set includes U+20AC (EUR), which latin-1 lacks.
    for codec in ("cp1252", "latin-1"):
        try:
            return text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def is_software_intern(job: dict) -> bool:
    title = _mojibake(job.get("title") or "")
    slugs = " ".join(r.get("slug", "") for r in job.get("roles") or [])

    if not (INTERNISH.search(title) or job.get("employment_type") == "internship"):
        return False

    software_title = bool(SOFTWARE.search(title))
    if EXCLUDE.search(title) and not software_title:
        return False
    return software_title or bool(SOFTWARE_SLUG.search(slugs))


def location_rank(job: dict, priority: list[str], allowed: list[str],
                  include_remote: bool) -> int | None:
    """Lower rank sorts first. None means the job is out of scope."""
    code = ((job.get("location") or {}).get("country_code") or "").upper()
    remote = job.get("workplace_type") == "remote"

    if code in priority:
        return priority.index(code)
    if code in allowed:
        return len(priority) + allowed.index(code)
    if remote and include_remote:
        return len(priority) + len(allowed) + 1
    return None


def select(jobs: list[dict], priority: list[str], allowed: list[str],
           include_remote: bool) -> list[dict]:
    """Filter to in-scope SWE internships, newest first within each location tier."""
    picked = []
    for job in jobs:
        if not is_software_intern(job):
            continue
        rank = location_rank(job, priority, allowed, include_remote)
        if rank is None:
            continue
        job = dict(job)
        job["title"] = _mojibake(job.get("title") or "")
        job["_rank"] = rank
        picked.append(job)
    # Stable sort: newest first, then group by location tier (priority countries lead).
    picked.sort(key=lambda j: j.get("published_at") or "", reverse=True)
    picked.sort(key=lambda j: j["_rank"])
    return _collapse_reposts(picked)


def _company_name(job: dict) -> str:
    company = job.get("company")
    if isinstance(company, dict):
        return company.get("name") or ""
    return company or job.get("company_name") or ""


def _collapse_reposts(jobs: list[dict]) -> list[dict]:
    """Drop repeats of the same role at the same company in the same city.

    Employers routinely post one opening several times under different ids
    (Huawei lists the identical co-op role 4x). Keeping only the first
    occurrence stops one employer from flooding a digest.
    """
    out, seen = [], set()
    for job in jobs:
        loc = job.get("location") or {}
        key = (
            _company_name(job).strip().lower(),
            re.sub(r"[^a-z0-9]+", " ", job["title"].lower()).strip(),
            (loc.get("city") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out
