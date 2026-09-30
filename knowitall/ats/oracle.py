"""Oracle Recruiting Cloud (Candidate Experience): the public REST search behind careers.jpmorgan.com, Uber's
careers site and many other large employers.

    GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList.workLocation,requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={site},limit={n},offset={k},sortBy=POSTING_DATES_DESC

The answer is {"items": [{"TotalJobsCount": N, "requisitionList": [...]}]}: one search result whose list holds the
page of postings (the top-level "hasMore" refers to the single search item, not to the postings). The site is the
"siteNumber" (CX_1001) or the site's URL name (UberCareers); both are accepted. A posting's page is
https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{Id}. Shape confirmed against live sites on 2026-09-30.
"""
from ..fetch import fetch_json, fetch_json_many
from ..normalize import make_job

PAGE_SIZE = 50          # the API allows up to 200 per page; 50 keeps each answer small and the first rows early
PARALLEL_PAGES = 4
SEARCH = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
          "&expand=requisitionList.workLocation,requisitionList.secondaryLocations"
          "&finder=findReqs;siteNumber={site},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC")
MAX_EXTRA_LOCATIONS = 4


def _url(detection, posting_id):
    extra = detection.extra
    return (f"https://{extra['host']}/hcmUI/CandidateExperience/{extra.get('lang') or 'en'}"
            f"/sites/{extra['site']}/job/{posting_id}")


def _page_url(detection, offset):
    return SEARCH.format(host=detection.extra["host"], site=detection.extra["site"], limit=PAGE_SIZE, offset=offset)


def _requisitions(result):
    """(total, postings) from one answer, or (None, None) when it is not a search result."""
    data = result.get("data") if result.get("status") == 200 else None
    items = data.get("items") if isinstance(data, dict) else None
    if not items or not isinstance(items[0], dict):
        return None, None
    search = items[0]
    postings = search.get("requisitionList")
    total = search.get("TotalJobsCount")
    if not isinstance(postings, list) or not isinstance(total, int):
        return None, None
    return total, postings


def _location(posting):
    names = [posting.get("PrimaryLocation")] + [
        place.get("Name") for place in (posting.get("secondaryLocations") or [])[:MAX_EXTRA_LOCATIONS]
        if isinstance(place, dict)]
    return [name for name in names if name]


def _structure(posting):
    """City/region/country from the work location the listing expands, falling back to the country code."""
    place = next((p for p in posting.get("workLocation") or [] if isinstance(p, dict) and (p.get("TownOrCity") or p.get("Country"))), {})
    return {"city": place.get("TownOrCity"), "region": place.get("Region2"),
            "country": place.get("Country") or posting.get("PrimaryLocationCountry")}


def _job(detection, site, posting):
    return make_job(
        company=site.name,
        title=posting.get("Title"),
        url=_url(detection, posting.get("Id")),
        location=_location(posting),
        workplace=posting.get("WorkplaceType") or None,
        employment_type=next((posting[k] for k in ("JobSchedule", "WorkerType", "ContractType", "JobType") if posting.get(k)), None),
        department=posting.get("JobFamily") or posting.get("JobFunction") or posting.get("Department") or posting.get("Organization"),
        posted=posting.get("PostedDate"),
        source="oracle",
        **_structure(posting),
    )


def fetch_jobs(ctx, detection, site):
    """Read the search page by page; each page's jobs are handed on (ctx.emit) as soon as they arrive."""
    first = fetch_json(ctx, _page_url(detection, 0))
    total, postings = _requisitions(first)
    if total is None:
        return None                                       # not an Oracle Recruiting site, or one that keeps its search private
    wanted = min(total, ctx.config.max_jobs)
    if total > ctx.config.max_jobs:
        ctx.log(f"oracle board has {total} jobs; fetching the first {ctx.config.max_jobs} (--max-jobs)")
        ctx.mark_truncated(f"oracle board has {total} jobs, read {ctx.config.max_jobs}")

    jobs = []

    def add(rows):
        batch = [_job(detection, site, p) for p in rows[:max(wanted - len(jobs), 0)] if isinstance(p, dict) and p.get("Id")]
        jobs.extend(batch)
        ctx.emit(batch)
        ctx.phase(f"Reading Oracle Recruiting jobs {len(jobs):,}/{wanted:,}…")

    add(postings)
    offsets = list(range(PAGE_SIZE, wanted, PAGE_SIZE))
    for start in range(0, len(offsets), PARALLEL_PAGES):
        if ctx.should_stop():
            break
        specs = [{"url": _page_url(detection, offset), "method": "GET"} for offset in offsets[start:start + PARALLEL_PAGES]]
        for result in fetch_json_many(ctx, specs, parallel=PARALLEL_PAGES):
            _, rows = _requisitions(result or {})
            if rows is None:
                ctx.mark_truncated("an Oracle results page could not be read")      # never call a partial read complete
            else:
                add(rows)
    return jobs
