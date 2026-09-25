"""Workday CXS jobs API: POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"""
import re

from ..fetch import fetch_json, fetch_json_many, log, settings
from ..normalize import make_job

PAGE_SIZE = 20  # Workday rejects limit > 20 with HTTP 400
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


def _remote(posting):
    remote_type = posting.get("remoteType")
    if remote_type:
        text = remote_type.lower()
        return "remote" in text and "hybrid" not in text
    return None


def fetch_jobs(detection, site):
    tenant, wd, board = detection.extra["tenant"], detection.extra["wd"], detection.extra["site"]
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{board}/jobs"

    first = fetch_json(api, method="POST", json=_payload(0))
    if first["status"] != 200 or not isinstance(first["data"], dict):
        return None
    total = first["data"].get("total") or 0  # only reliable on the first page
    postings = list(first["data"].get("jobPostings") or [])

    wanted = min(total, settings.max_jobs)
    if total > settings.max_jobs:
        log(f"workday board has {total} jobs; fetching the first {settings.max_jobs} (--max-jobs)")
    offsets = list(range(PAGE_SIZE, wanted, PAGE_SIZE))
    specs = [{"url": api, "method": "POST", "json": _payload(offset)} for offset in offsets]
    for result in fetch_json_many(specs, parallel=4):
        if result and result["status"] == 200 and isinstance(result["data"], dict):
            postings.extend(result["data"].get("jobPostings") or [])

    jobs = []
    for posting in postings[:wanted]:
        jobs.append(make_job(
            company=site.name,
            title=posting.get("title"),
            url=f"{base}/{board}{posting.get('externalPath', '')}",
            location=_location(posting),
            remote=_remote(posting),
            department=None,  # not included in Workday's list response
            posted=posting.get("postedOn"),  # relative text, e.g. "Posted Today"
            source="workday",
        ))
    return jobs
