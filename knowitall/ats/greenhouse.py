"""Greenhouse public Job Board API: https://boards-api.greenhouse.io/v1/boards/{token}/..."""
import re

from ..fetch import fetch_json
from ..normalize import make_job

API = "https://boards-api.greenhouse.io/v1/boards/{token}"
WORKPLACE_FIELD_RE = re.compile(r"workplace|remote|location type", re.I)
EMPLOYMENT_FIELD_RE = re.compile(r"employment type|job type|contract type|worker type|commitment", re.I)


def board_name(ctx, detection):
    result = fetch_json(ctx, API.format(token=detection.token))
    if result["status"] == 200 and isinstance(result["data"], dict):
        return result["data"].get("name")
    return None


def _metadata(job, pattern):
    # Workplace and employment type are per-company custom fields, e.g. {"name": "Workplace Type", "value": "Hybrid"}.
    for field in job.get("metadata") or []:
        if pattern.search(str(field.get("name"))) and isinstance(field.get("value"), str):
            return field["value"]
    return None


def fetch_jobs(ctx, detection, site):
    base = API.format(token=detection.token)
    listing = fetch_json(ctx, base + "/jobs")
    if listing["status"] != 200 or not isinstance(listing["data"], dict):
        return None

    # /jobs has no departments (only with ?content=true, which also returns every full description),
    # so map job id -> department from the lighter /departments endpoint.
    department_of = {}
    departments = fetch_json(ctx, base + "/departments")
    if departments["status"] == 200 and isinstance(departments["data"], dict):
        for department in departments["data"].get("departments") or []:
            name = department.get("name")
            if not name or name.lower() == "no department":
                continue
            for job in department.get("jobs") or []:
                department_of.setdefault(job.get("id"), name)

    jobs = []
    for job in listing["data"].get("jobs") or []:
        jobs.append(make_job(
            company=job.get("company_name") or site.name,
            title=job.get("title"),
            url=job.get("absolute_url"),
            location=(job.get("location") or {}).get("name"),
            workplace=_metadata(job, WORKPLACE_FIELD_RE),
            employment_type=_metadata(job, EMPLOYMENT_FIELD_RE),
            department=department_of.get(job.get("id")),
            posted=job.get("first_published") or job.get("updated_at"),
            source="greenhouse",
        ))
    ctx.emit(jobs)
    return jobs
