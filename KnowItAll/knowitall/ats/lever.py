"""Lever public Postings API: https://api.lever.co/v0/postings/{token}?mode=json (EU: api.eu.lever.co)."""
from ..fetch import fetch_json, fetch_page, page_ok, page_title
from ..normalize import make_job, workplace_to_remote


def _hosts(detection):
    eu = detection.extra.get("region") == "eu"
    return ("api.eu.lever.co", "jobs.eu.lever.co") if eu else ("api.lever.co", "jobs.lever.co")


def board_name(detection):
    # The API has no company name; the hosted board's <title> does ("Palantir Technologies").
    page = fetch_page(f"https://{_hosts(detection)[1]}/{detection.token}")
    return page_title(page) if page_ok(page) else None


def fetch_jobs(detection, site):
    api_host = _hosts(detection)[0]
    result = fetch_json(f"https://{api_host}/v0/postings/{detection.token}?mode=json")
    if result["status"] != 200 or not isinstance(result["data"], list):
        return None

    jobs = []
    for posting in result["data"]:
        categories = posting.get("categories") or {}
        locations = categories.get("allLocations") or categories.get("location")
        jobs.append(make_job(
            company=site.name,
            title=posting.get("text"),
            url=posting.get("hostedUrl"),
            location=locations,
            remote=workplace_to_remote(posting.get("workplaceType")),
            department=categories.get("department") or categories.get("team"),
            posted=posting.get("createdAt"),
            source="lever",
        ))
    return jobs
