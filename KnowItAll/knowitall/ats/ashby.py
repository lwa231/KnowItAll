"""Ashby public Job Board API: https://api.ashbyhq.com/posting-api/job-board/{token}"""
from ..fetch import fetch_json, fetch_page, page_ok, page_title
from ..normalize import make_job, workplace_to_remote

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def board_name(detection):
    # The API has no company name; the hosted board's <title> does ("Ramp Jobs").
    page = fetch_page(f"https://jobs.ashbyhq.com/{detection.token}")
    return page_title(page) if page_ok(page) else None


def fetch_jobs(detection, site):
    result = fetch_json(API.format(token=detection.token))
    if result["status"] != 200 or not isinstance(result["data"], dict) or "jobs" not in result["data"]:
        return None

    jobs = []
    for job in result["data"]["jobs"]:
        if job.get("isListed") is False:
            continue
        locations = [job.get("location")] + [s.get("location") for s in job.get("secondaryLocations") or []]
        jobs.append(make_job(
            company=site.name,
            title=job.get("title"),
            url=job.get("jobUrl"),
            location=locations,
            # workplaceType, not isRemote: isRemote was true on a job marked "Hybrid".
            remote=workplace_to_remote(job.get("workplaceType")),
            department=job.get("department") or job.get("team"),
            source="ashby",
        ))
    return jobs
