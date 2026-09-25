"""SmartRecruiters public Posting API: https://api.smartrecruiters.com/v1/companies/{id}/postings"""
from ..fetch import fetch_json, settings
from ..normalize import make_job

API = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit={limit}&offset={offset}"
PAGE_SIZE = 100


def _remote(location):
    if "remote" not in location and "hybrid" not in location:
        return None
    if location.get("remote"):
        return True
    return False


def fetch_jobs(detection, site):
    postings, offset, total = [], 0, None
    while total is None or offset < min(total, settings.max_jobs):
        result = fetch_json(API.format(token=detection.token, limit=PAGE_SIZE, offset=offset))
        if result["status"] != 200 or not isinstance(result["data"], dict):
            break
        total = result["data"].get("totalFound") or 0
        page = result["data"].get("content") or []
        postings.extend(page)
        if not page:
            break
        offset += PAGE_SIZE
    if not postings:
        # Unknown companies return 200 with totalFound 0 (not 404), so empty means "not this ATS".
        return None

    jobs = []
    for posting in postings[: settings.max_jobs]:
        location = posting.get("location") or {}
        jobs.append(make_job(
            company=(posting.get("company") or {}).get("name") or site.name,
            title=posting.get("name"),
            url=f"https://jobs.smartrecruiters.com/{detection.token}/{posting.get('id')}",
            location=location.get("fullLocation") or [location.get("city"), location.get("country")],
            remote=_remote(location),
            department=(posting.get("department") or {}).get("label"),
            posted=posting.get("releasedDate") or posting.get("createdOn"),
            source="smartrecruiters",
        ))
    return jobs
