"""Postings read from the JSON a rendered careers page loaded from its own back end.

A JavaScript careers site (Uber-style) has no job links in its HTML: the page asks an internal API for the
listing and draws it. BrowserPool records those responses (listing mode); this module looks in them for an array
of objects that are job postings and maps each to the common job schema (source "json-sniffed").

It is deliberately generic, and deliberately careful: an array counts only when most of its objects have a title
and something that identifies a posting, so a list of filters ("Engineering, 120 jobs") is not mistaken for jobs.
A site whose keys are too abbreviated for that gets an adapter in knowitall/adapters/ instead.
"""
import json
import re
from urllib.parse import urljoin, urlparse

from . import adapters
from .fetch import log
from .normalize import clean, employment_type_of, make_job

TITLE_KEYS = ("title", "name", "jobTitle", "job_title", "text", "position", "positionTitle", "requisitionTitle")
ID_KEYS = ("id", "jobId", "job_id", "reqId", "req_id", "requisitionId", "requisition_id", "jobReqId", "externalId",
           "postingId", "jobPostingId", "slug")
URL_KEYS = ("url", "applyUrl", "apply_url", "hostedUrl", "jobUrl", "job_url", "absolute_url", "canonicalUrl",
            "externalPath", "detailsUrl", "detailUrl", "jobDetailUrl", "link", "href", "path")
LOCATION_KEYS = ("location", "locations", "locationName", "primaryLocation", "jobLocation", "locationsText", "offices",
                 "address", "city")
DEPARTMENT_KEYS = ("department", "departments", "team", "teams", "function", "category", "organization", "businessUnit",
                   "jobFamily", "jobFunction")
DATE_KEYS = ("datePosted", "postedDate", "posted", "postedOn", "publishedAt", "published_at", "createdAt", "created_at",
             "updatedAt", "postingDate", "creationDate", "date")
TYPE_KEYS = ("employmentType", "employment_type", "jobType", "job_type", "workerType", "timeType", "commitment")
WORKPLACE_KEYS = ("workplaceType", "workplace_type", "workplace", "locationType", "remoteType")
COUNT_KEYS = ("count", "total", "numberOfJobs", "jobCount", "jobsCount")
# What makes an object a *job* rather than any titled, linked thing (a news story, a filter, a team page): an id that
# is specifically a job's, or an attribute only jobs have. "category" and "date" are deliberately not evidence.
JOB_ID_KEYS = ("jobId", "job_id", "reqId", "req_id", "requisitionId", "requisition_id", "jobReqId", "postingId",
               "jobPostingId", "jobRequisitionId")
DEPARTMENT_EVIDENCE = ("department", "departments", "team", "teams", "function", "jobFamily", "jobFunction", "businessUnit")

MIN_ITEMS = 3                 # fewer than this is not a listing
MIN_SHARE = 0.6               # this share of an array's objects must look like postings
MAX_DEPTH = 8
MAX_NODES = 200_000           # a runaway payload must not stall a scan


def _lookup(item, keys):
    """First non-empty value of `item` under any of `keys` (case-insensitive), as (key, value)."""
    lowered = {str(k).lower(): k for k in item}
    for key in keys:
        real = lowered.get(key.lower())
        if real is not None and item[real] not in (None, "", [], {}):
            return key, item[real]
    return None, None


def _text(value):
    """Readable text from a string, number, {name/label/...} object or a list of those."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return clean(str(value))
    if isinstance(value, dict):
        for key in ("name", "label", "title", "text", "value", "displayName"):
            if isinstance(value.get(key), (str, int, float)) and value.get(key) != "":
                return clean(str(value[key]))
        parts = [_text(value.get(k)) for k in ("city", "state", "region", "province", "country", "countryName")]
        parts = [p for p in parts if p]
        return ", ".join(parts) or None
    if isinstance(value, (list, tuple)):
        parts = [t for t in (_text(v) for v in value) if t]
        return " | ".join(dict.fromkeys(parts)) or None
    return None


def _is_url_like(value):
    return isinstance(value, str) and (value.startswith(("http://", "https://")) or value.startswith("/"))


def _has_job_attributes(item):
    return any(_lookup(item, keys)[0] for keys in (LOCATION_KEYS, DEPARTMENT_EVIDENCE, TYPE_KEYS, WORKPLACE_KEYS))


def _looks_like_posting(item):
    if not isinstance(item, dict):
        return False
    _, title = _lookup(item, TITLE_KEYS)
    if not isinstance(title, str) or len(title.strip()) < 3 or not re.search(r"[A-Za-z]", title):
        return False
    _, url = _lookup(item, URL_KEYS)
    identifier_key, identifier = _lookup(item, ID_KEYS)
    identified = _is_url_like(url) or (identifier_key is not None and not isinstance(identifier, (dict, list)))
    if not identified:
        return False
    # A title and a link also describe a news story, and a title and an id a filter ({"id": "US", "name": "United
    # States", "count": 4}). A posting has an id that is a job's, or something only jobs have (a location...).
    return bool(_lookup(item, JOB_ID_KEYS)[0]) or (_has_job_attributes(item) and not _lookup(item, COUNT_KEYS)[0])


def find_arrays(data):
    """Every array of at least MIN_ITEMS objects in `data`, most of which look like postings: [(path, items)]."""
    found, budget = [], [MAX_NODES]

    def walk(node, path, depth):
        budget[0] -= 1
        if budget[0] <= 0 or depth > MAX_DEPTH:
            return
        if isinstance(node, list):
            objects = [x for x in node if isinstance(x, dict)]
            if len(objects) >= MIN_ITEMS:
                sample = objects[:50]
                if sum(1 for x in sample if _looks_like_posting(x)) >= MIN_SHARE * len(sample):
                    found.append((path, objects))
                    return                                  # postings do not nest further postings
            for index, child in enumerate(node[:200]):
                walk(child, f"{path}[]", depth + 1)
        elif isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key), depth + 1)

    walk(data, "", 0)
    return found


def _employment(item):
    """Employment type text. A bare "type" key is only trusted when its value is real employment vocabulary
    ("Full-Time"), since "type" can mean anything."""
    value = _text(_lookup(item, TYPE_KEYS)[1])
    if value:
        return value
    loose = _text(item.get("type"))
    return loose if loose and employment_type_of(loose) not in (None, "other") else None


def _posting_id(item):
    _, identifier = _lookup(item, ID_KEYS)
    return None if identifier is None or isinstance(identifier, (dict, list)) else str(identifier)


def _to_job(item, page_url, site):
    title = _text(_lookup(item, TITLE_KEYS)[1])
    _, url = _lookup(item, URL_KEYS)
    identifier = _posting_id(item)
    if _is_url_like(url):
        url = urljoin(page_url, url)
    elif identifier:
        url = f"{page_url.split('#')[0]}#job-{re.sub(r'[^A-Za-z0-9._~-]+', '-', identifier)}"
    else:
        return None
    location_key, location = _lookup(item, LOCATION_KEYS)
    structure = {}
    if isinstance(location, dict):                            # {"city": ..., "state": ..., "country": ...}
        for field, keys in (("city", ("city", "locality")), ("region", ("state", "region", "province", "stateProvince")),
                            ("country", ("country", "countryCode", "countryName"))):
            value = _text(_lookup(location, keys)[1])
            if value:
                structure[field] = value
    elif location_key == "city":
        structure = {"city": _text(location)}
        for field, keys in (("region", ("state", "region", "province")), ("country", ("country", "countryCode"))):
            value = _text(_lookup(item, keys)[1])
            if value:
                structure[field] = value
        location = ", ".join(v for v in structure.values() if v)
    remote = _lookup(item, ("isRemote", "remote"))[1]
    return make_job(
        company=site.name,
        title=title,
        url=url,
        location=_text(location),
        remote=True if remote is True else None,               # False only ever meant "not fully remote"
        department=_text(_lookup(item, DEPARTMENT_KEYS)[1]),
        posted=_lookup(item, DATE_KEYS)[1],
        employment_type=_employment(item),
        workplace=_text(_lookup(item, WORKPLACE_KEYS)[1]),
        source="json-sniffed",
        **structure,
    )


def _signature(payload_url, path):
    """Two responses from the same endpoint and place in the JSON are pages of one listing."""
    return (urlparse(payload_url or "").path, path)


def extract(ctx, payloads, page_url, site):
    """Jobs from the captured JSON responses ([{"url": ..., "data": ...}]); [] when none holds a listing.

    Responses from one endpoint (page 1, page 2 after a "load more" click) are merged; when several endpoints
    hold postings the one with the most postings wins."""
    adapter = adapters.for_site(site)
    if adapter:
        jobs = adapter(payloads, page_url, site)
        if jobs:
            ctx.log(f"read {len(jobs)} postings with the {site.domain} adapter")
            return jobs
    groups = {}
    for payload in payloads:
        for path, items in find_arrays(payload.get("data")):
            groups.setdefault(_signature(payload.get("url"), path), []).extend(items)
    best = []
    for items in groups.values():
        jobs, seen = [], set()
        for item in items:
            job = _to_job(item, page_url, site)
            if job and job["title"] and job["url"] not in seen:
                seen.add(job["url"])
                jobs.append(job)
        if len(jobs) > len(best):
            best = jobs
    if len(best) < MIN_ITEMS:
        return []
    ctx.log(f"read {len(best)} postings from data the page loaded")
    return best


# ---------- what a rendered page loaded (for building adapters) ----------

def describe(payloads):
    """One line per captured response: where it came from, its top-level keys, and the size of its arrays."""
    lines = []
    for payload in payloads:
        data = payload.get("data")
        arrays = {}

        def walk(node, path, depth=0):
            if depth > 5:
                return
            if isinstance(node, list):
                arrays[path or "[]"] = len(node)
                if node and isinstance(node[0], (dict, list)):
                    walk(node[0], f"{path}[]", depth + 1)
            elif isinstance(node, dict):
                for key, child in node.items():
                    walk(child, f"{path}.{key}" if path else str(key), depth + 1)

        walk(data, "")
        keys = list(data)[:12] if isinstance(data, dict) else f"list[{len(data)}]" if isinstance(data, list) else type(data).__name__
        lines.append(f"{payload.get('url')}  keys={keys}  arrays={dict(list(arrays.items())[:10])}")
    return lines


def log_payloads(ctx, payloads):
    ctx.log(f"the page loaded {len(payloads)} JSON response(s)")
    for line in describe(payloads):
        log(f"  payload: {line}")
