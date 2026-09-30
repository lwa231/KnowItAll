"""Workday CXS jobs API: POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"""
import re

from .. import geo
from ..fetch import fetch_json, fetch_json_many
from ..normalize import make_job

PAGE_SIZE = 20  # Workday rejects limit > 20 with HTTP 400
PARALLEL_PAGES = 4
MULTI_LOCATION_RE = re.compile(r"^(\d+)\s+Locations$", re.I)


def _payload(offset):
    return {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}


def _location(posting):
    text = posting.get("locationsText") or ""
    multi = MULTI_LOCATION_RE.match(text)
    if not multi:
        return text
    # "4 Locations": the primary location is the first segment of externalPath, e.g. /job/US-CA-Santa-Clara/...
    parts = (posting.get("externalPath") or "").split("/")
    primary = parts[2].replace("-", " ") if len(parts) > 2 and parts[1] == "job" else None
    return f"{primary} (+{int(multi.group(1)) - 1} more)" if primary else text


SLUG_COUNTRY_STATE_RE = re.compile(r"^([A-Z]{2})-([A-Z]{2})-(.+)$")     # US-CA-Santa-Clara
SLUG_COUNTRY_RE = re.compile(r"^([A-Z]{2})-(.+)$")                       # GB-London
WORKPLACE_SLUG_RE = re.compile(r"^(remote|hybrid|on-?site|home[- ]?office|flex)(-|$)", re.I)
# A country name at the start of a slug is only trusted when it cannot also begin a city name
# ("Jersey-City" is not the Channel Island; "Panama-City" is in Florida).
NAME_PREFIX_DENY = {"jersey", "georgia", "jordan", "chad", "panama", "guinea", "niger", "turkey"}


def _city_from(words):
    """City text from slug words, or None when the slug says 'Remote' instead of naming a city."""
    text = "-".join(words)
    if not text or WORKPLACE_SLUG_RE.match(text):
        return None
    return text.replace("-", " ")


def _slug_geo(posting):
    """Structure hidden in the posting path, e.g. /job/US-CA-Santa-Clara/Title_R1 -> Santa Clara, California, US.

    Three shapes Workday tenants use consistently: US-CA-City, GB-City and Israel-Tel-Aviv (country name first).
    Ambiguous prefixes are refused rather than guessed: 'CA-San-Jose' is not read as Canada (a two-letter
    prefix that is also a US state/Canadian province code), and 'US-CA-Remote' has no city called Remote.
    """
    parts = (posting.get("externalPath") or "").split("/")
    if len(parts) < 3 or parts[1] != "job":
        return {}
    slug = parts[2]
    match = SLUG_COUNTRY_STATE_RE.match(slug)
    if match and match.group(1) in ("US", "CA") and geo.subdivision_name(match.group(1), match.group(2)):
        found = {"country": match.group(1), "region": match.group(2)}
        city = _city_from([match.group(3)])
        return {**found, **({"city": city} if city else {})}
    match = SLUG_COUNTRY_RE.match(slug)
    if match and geo.normalize_country(match.group(1)) and not geo.is_subdivision_code(match.group(1)):
        city = _city_from([match.group(2)])
        return {"country": match.group(1), **({"city": city} if city else {})}
    words = slug.split("-")
    for size in (3, 2, 1):
        if len(words) < size:
            continue
        name = " ".join(words[:size])
        country = geo.normalize_country(name)
        if country and name.lower() not in NAME_PREFIX_DENY and not (len(name) == 2 and name.isupper()):
            found = {"country": country}
            rest = words[size:]
            if country in ("US", "CA") and len(rest) > 1 and geo.subdivision_name(country, rest[0]):
                found["region"], rest = rest[0], rest[1:]
            city = _city_from(rest)
            return {**found, **({"city": city} if city else {})}
    return {}


def _job(base, board, site, posting):
    return make_job(
        company=site.name,
        title=posting.get("title"),
        url=f"{base}/{board}{posting.get('externalPath', '')}",
        location=_location(posting),
        workplace=posting.get("remoteType"),
        **_slug_geo(posting),
        department=None,  # not included in Workday's list response
        posted=posting.get("postedOn"),  # relative text, e.g. "Posted Today"
        source="workday",
    )


def fetch_jobs(ctx, detection, site):
    """Read the board page by page; each page's jobs are handed on (ctx.emit) as soon as they arrive."""
    tenant, wd, board = detection.extra["tenant"], detection.extra["wd"], detection.extra["site"]
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{board}/jobs"

    first = fetch_json(ctx, api, method="POST", json=_payload(0))
    if first["status"] != 200 or not isinstance(first["data"], dict):
        return None
    total = first["data"].get("total") or 0  # only reliable on the first page
    wanted = min(total, ctx.config.max_jobs)
    if total > ctx.config.max_jobs:
        ctx.log(f"workday board has {total} jobs; fetching the first {ctx.config.max_jobs} (--max-jobs)")
        ctx.mark_truncated(f"workday board has {total} jobs, read {ctx.config.max_jobs}")

    jobs = []

    def add(postings):
        batch = [_job(base, board, site, p) for p in postings[:max(wanted - len(jobs), 0)]]
        jobs.extend(batch)
        ctx.emit(batch)
        ctx.phase(f"Reading Workday jobs {len(jobs):,}/{wanted:,}…")

    add(first["data"].get("jobPostings") or [])
    specs = [{"url": api, "method": "POST", "json": _payload(offset)} for offset in range(PAGE_SIZE, wanted, PAGE_SIZE)]
    for start in range(0, len(specs), PARALLEL_PAGES):
        if ctx.should_stop():
            break
        for result in fetch_json_many(ctx, specs[start:start + PARALLEL_PAGES], parallel=PARALLEL_PAGES):
            if result and result["status"] == 200 and isinstance(result["data"], dict):
                add(result["data"].get("jobPostings") or [])
    return jobs
