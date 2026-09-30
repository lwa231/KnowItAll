"""SmartRecruiters public Posting API: https://api.smartrecruiters.com/v1/companies/{id}/postings"""
from ..fetch import fetch_json
from ..normalize import make_job

API = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit={limit}&offset={offset}"
PAGE_SIZE = 100


def _workplace(location):
    if location.get("hybrid"):
        return "hybrid"
    if location.get("remote"):
        return "remote"
    if "remote" in location or "hybrid" in location:
        return "onsite"                      # the flags are present and both false
    return None


def _job(posting, detection, site):
    location = posting.get("location") or {}
    return make_job(
        company=(posting.get("company") or {}).get("name") or site.name,
        title=posting.get("name"),
        url=f"https://jobs.smartrecruiters.com/{detection.token}/{posting.get('id')}",
        location=location.get("fullLocation") or [location.get("city"), location.get("country")],
        workplace=_workplace(location),
        employment_type=(posting.get("typeOfEmployment") or {}).get("label")
        or (posting.get("typeOfEmployment") or {}).get("id"),
        city=location.get("city"),
        region=location.get("region"),
        country=location.get("country"),
        department=(posting.get("department") or {}).get("label"),
        posted=posting.get("releasedDate") or posting.get("createdOn"),
        source="smartrecruiters",
    )


def fetch_jobs(ctx, detection, site):
    """Page through the postings; each page's jobs are handed on (ctx.emit) as soon as it arrives."""
    jobs, offset, total = [], 0, None
    while (total is None or offset < min(total, ctx.config.max_jobs)) and not ctx.should_stop():
        result = fetch_json(ctx, API.format(token=detection.token, limit=PAGE_SIZE, offset=offset))
        if result["status"] != 200 or not isinstance(result["data"], dict):
            break
        total = result["data"].get("totalFound") or 0
        page = result["data"].get("content") or []
        if not page:
            break
        batch = [_job(p, detection, site) for p in page[:max(ctx.config.max_jobs - len(jobs), 0)]]
        jobs.extend(batch)
        ctx.emit(batch)
        ctx.phase(f"Reading SmartRecruiters jobs {len(jobs):,}/{min(total, ctx.config.max_jobs):,}…")
        offset += PAGE_SIZE
    if not jobs:
        # Unknown companies return 200 with totalFound 0 (not 404), so empty means "not this ATS".
        return None
    if total and total > ctx.config.max_jobs:
        ctx.mark_truncated(f"smartrecruiters board has {total} jobs, read {ctx.config.max_jobs}")
    return jobs
