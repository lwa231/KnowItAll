import pytest

from knowitall.ats import ashby, greenhouse, lever, smartrecruiters, workday
from knowitall.ats_detect import Detection
from knowitall.context import ScanContext
from knowitall.discovery import Site
from tests.conftest import load_fixture

CTX = ScanContext()
SITE = Site(input_url="https://acme.com", root_url="https://acme.com/", host="acme.com",
            domain="acme.com", slug="acme", name="Acme")


def test_greenhouse(fake_fetch):
    fake_fetch(greenhouse, {
        "/departments": load_fixture("greenhouse_departments.json"),
        "/jobs": load_fixture("greenhouse_jobs.json"),
    })
    jobs = greenhouse.fetch_jobs(CTX, Detection("greenhouse", "acme"), SITE)
    assert [j["title"] for j in jobs] == ["Backend Engineer", "Designer"]
    first, second = jobs
    assert first["department"] == "Engineering"
    assert second["department"] is None            # "No Department" is dropped
    assert first["location"] == "San Francisco, CA"
    assert first["posted"] == "2025-03-01T15:00:00+00:00"   # first_published preferred
    assert first["remote"] is False                # Hybrid via custom metadata (collapsed today)
    assert second["remote"] is True                # inferred from "Remote - US"
    assert all(j["source"] == "greenhouse" and j["company"] == "Acme Corp" for j in jobs)


def test_greenhouse_missing_board_returns_none(fake_fetch):
    fake_fetch(greenhouse, {})
    assert greenhouse.fetch_jobs(CTX, Detection("greenhouse", "nope"), SITE) is None


def test_lever(fake_fetch):
    fake_fetch(lever, {"/v0/postings/acme": load_fixture("lever_postings.json")})
    jobs = lever.fetch_jobs(CTX, Detection("lever", "acme"), SITE)
    assert len(jobs) == 2
    assert jobs[0]["location"] == "London | Berlin"
    assert jobs[0]["department"] == "Data"
    assert jobs[0]["remote"] is False              # hybrid
    assert jobs[0]["posted"].startswith("2025-02-19")
    assert jobs[1]["remote"] is True


def test_lever_uses_eu_host_when_flagged(fake_fetch, monkeypatch):
    seen = []
    monkeypatch.setattr(lever, "fetch_json",
                        lambda ctx, url, **kw: seen.append(url) or {"status": 200, "data": []})
    lever.fetch_jobs(CTX, Detection("lever", "acme", extra={"region": "eu"}), SITE)
    assert seen == ["https://api.eu.lever.co/v0/postings/acme?mode=json"]


def test_ashby_skips_unlisted_and_uses_workplace_not_isremote(fake_fetch):
    fake_fetch(ashby, {"/job-board/acme": load_fixture("ashby_board.json")})
    jobs = ashby.fetch_jobs(CTX, Detection("ashby", "acme"), SITE)
    assert [j["title"] for j in jobs] == ["Platform Engineer"]
    job = jobs[0]
    assert job["location"] == "New York | Toronto"
    assert job["remote"] is False                  # "Hybrid" wins although isRemote is true
    assert job["department"] == "Engineering"


def test_workday_pages_and_multi_location(fake_fetch, monkeypatch):
    page = load_fixture("workday_page1.json")
    fake_fetch(workday, {"/wday/cxs/acme/careers/jobs": page})
    detection = Detection("workday", "acme", extra={"tenant": "acme", "wd": "wd1", "site": "careers"})
    jobs = workday.fetch_jobs(CTX, detection, SITE)
    assert [j["title"] for j in jobs] == ["Chip Designer", "Field Engineer", "Remote Analyst"]
    assert jobs[0]["location"] == "US CA Santa Clara (+3 more)"
    assert jobs[0]["url"] == "https://acme.wd1.myworkdayjobs.com/careers/job/US-CA-Santa-Clara/Chip-Designer_R1"
    assert jobs[0]["remote"] is False              # hybrid
    assert jobs[1]["remote"] is None               # no remoteType and no "remote" in the text
    assert jobs[2]["remote"] is True
    assert all(j["department"] is None for j in jobs)


def test_smartrecruiters(fake_fetch):
    fake_fetch(smartrecruiters, {"/companies/acme/postings": load_fixture("smartrecruiters_postings.json")})
    jobs = smartrecruiters.fetch_jobs(CTX, Detection("smartrecruiters", "acme"), SITE)
    assert len(jobs) == 2
    assert jobs[0]["location"] == "Dublin, Ireland"
    assert jobs[0]["department"] == "People"
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/acme/700"


def test_smartrecruiters_unknown_company_is_none(fake_fetch):
    fake_fetch(smartrecruiters, {"/companies/": {"totalFound": 0, "content": []}})
    assert smartrecruiters.fetch_jobs(CTX, Detection("smartrecruiters", "ghost"), SITE) is None
