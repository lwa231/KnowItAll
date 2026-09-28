import json

import pytest

from knowitall import generic, normalize as n
from knowitall.ats import ashby, greenhouse, lever, smartrecruiters, workday
from knowitall.ats_detect import Detection
from knowitall.context import ScanContext
from knowitall.discovery import Site
from tests.conftest import load_fixture

CTX = ScanContext()
SITE = Site(input_url="https://acme.com", root_url="https://acme.com/", host="acme.com",
            domain="acme.com", slug="acme", name="Acme")


def geo_of(job):
    return job["city"], job["region"], job["country"], job["geo_confidence"]


# ---------- normalisers ----------

@pytest.mark.parametrize("value, expected", [
    ("Remote", "remote"), ("remote", "remote"), ("Fully Remote", "remote"), ("TELECOMMUTE", "remote"),
    ("Hybrid", "hybrid"), ("hybrid", "hybrid"), ("Flex", "hybrid"), ("Flexible", "hybrid"),
    ("On-site", "onsite"), ("OnSite", "onsite"), ("onsite", "onsite"), ("In Office", "onsite"), ("Office", "onsite"),
    ("Unspecified", None), ("unknown", None), ("", None), (None, None), ("banana", None),
])
def test_workplace_of(value, expected):
    assert n.workplace_of(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("Full-time", "full_time"), ("FULL_TIME", "full_time"), ("FullTime", "full_time"), ("Permanent", "full_time"),
    ("Regular Full Time", "full_time"), ("Part-time", "part_time"), ("PART_TIME", "part_time"),
    ("Contract", "contract"), ("Contractor", "contract"), ("Temporary", "contract"), ("Fixed-term", "contract"),
    ("Freelance", "contract"), ("Intern", "intern"), ("Internship", "intern"), ("Co-op", "intern"),
    ("Volunteer", "other"), ("PER_DIEM", "other"), ("Unspecified", None), ("", None), (None, None),
])
def test_employment_type_of(value, expected):
    assert n.employment_type_of(value) == expected


def test_remote_is_derived_from_workplace_and_hybrid_is_no_longer_lost():
    for workplace, remote in (("remote", True), ("hybrid", False), ("onsite", False), (None, None)):
        job = n.make_job("Acme", "Eng", "https://x.com/1", workplace=workplace)
        assert job["workplace"] == workplace and job["remote"] is remote
    hybrid = n.make_job("Acme", "Eng", "https://x.com/1", workplace="Hybrid")
    assert hybrid["workplace"] == "hybrid" and hybrid["remote"] is False       # distinguishable from onsite


def test_legacy_remote_argument_still_works():
    assert n.make_job("A", "E", "https://x.com/1", remote=True)["workplace"] == "remote"
    assert n.make_job("A", "E", "https://x.com/2", remote=False)["workplace"] == "onsite"


def test_workplace_inferred_from_text_only_when_unambiguous():
    assert n.make_job("A", "E", "u", location="Remote - US")["workplace"] == "remote"
    assert n.make_job("A", "E (Hybrid)", "u", location="London")["workplace"] == "hybrid"
    assert n.make_job("A", "E", "u", location="Remote or Hybrid")["workplace"] is None
    assert n.make_job("A", "E", "u", location="Berlin")["workplace"] is None      # on-site is never guessed
    assert n.make_job("A", "E", "u", location="Remote", workplace="onsite")["workplace"] == "onsite"   # feed wins


def test_invalid_employment_type_is_normalised_not_kept_raw():
    assert n.make_job("A", "E", "u", employment_type="FULL_TIME")["employment_type"] == "full_time"
    assert n.make_job("A", "E", "u", employment_type="full_time")["employment_type"] == "full_time"
    assert n.make_job("A", "E", "u")["employment_type"] is None


# ---------- geo confidence ----------

def test_feed_structure_wins_and_is_marked_feed():
    job = n.make_job("A", "E", "u", location="Somewhere odd", city="Lyon", region="Rhône", country="fr")
    assert geo_of(job) == ("Lyon", "Rhône", "FR", "feed")


def test_feed_country_only_takes_city_from_text_when_it_agrees():
    job = n.make_job("A", "E", "u", location="London, United Kingdom", country="GB")
    assert geo_of(job) == ("London", None, "GB", "feed")
    contradicts = n.make_job("A", "E", "u", location="Berlin, Germany", country="FR")
    assert geo_of(contradicts) == (None, None, "FR", "feed")           # the text never overrides the feed


def test_parsed_structure_is_marked_parsed_and_unknown_is_null():
    parsed = n.make_job("A", "E", "u", location="Austin, TX")
    assert geo_of(parsed) == ("Austin", "Texas", "US", "parsed")
    unknown = n.make_job("A", "E", "u", location="Paris")
    assert geo_of(unknown) == (None, None, None, None)


def test_unrecognised_feed_country_is_not_invented():
    job = n.make_job("A", "E", "u", location="Nowhere", city="Gotham", country="Atlantis")
    assert geo_of(job) == ("Gotham", None, None, "feed")


# ---------- each reader ----------

def test_greenhouse_fields(fake_fetch):
    fake_fetch(greenhouse, {"/departments": load_fixture("greenhouse_departments.json"),
                            "/jobs": load_fixture("greenhouse_jobs.json")})
    first, second = greenhouse.fetch_jobs(CTX, Detection("greenhouse", "acme"), SITE)
    assert first["workplace"] == "hybrid" and first["remote"] is False
    assert first["employment_type"] == "full_time"
    assert geo_of(first) == ("San Francisco", "California", "US", "parsed")
    assert second["workplace"] == "remote" and second["employment_type"] is None
    assert geo_of(second) == (None, None, "US", "parsed")               # "Remote - US"


def test_lever_fields(fake_fetch):
    fake_fetch(lever, {"/v0/postings/acme": load_fixture("lever_postings.json")})
    first, second = lever.fetch_jobs(CTX, Detection("lever", "acme"), SITE)
    assert first["workplace"] == "hybrid"
    assert first["employment_type"] == "full_time"
    assert geo_of(first) == ("London", None, "GB", "feed")              # country from the feed, city agrees
    assert second["workplace"] == "remote" and second["employment_type"] == "intern"
    assert geo_of(second)[3] is None


def test_ashby_fields(fake_fetch):
    fake_fetch(ashby, {"/job-board/acme": load_fixture("ashby_board.json")})
    (job,) = ashby.fetch_jobs(CTX, Detection("ashby", "acme"), SITE)
    assert job["workplace"] == "hybrid"
    assert job["employment_type"] == "full_time"
    assert geo_of(job) == ("New York", "New York", "US", "feed")        # "USA" normalised to US


def test_workday_fields(fake_fetch):
    fake_fetch(workday, {"/wday/cxs/acme/careers/jobs": load_fixture("workday_page1.json")})
    detection = Detection("workday", "acme", extra={"tenant": "acme", "wd": "wd1", "site": "careers"})
    hybrid, austin, remote = workday.fetch_jobs(CTX, detection, SITE)
    assert hybrid["workplace"] == "hybrid"
    assert hybrid["country"] == "US" and hybrid["region"] == "California" and hybrid["city"] == "Santa Clara"
    assert hybrid["geo_confidence"] == "feed"                            # from the US-CA-Santa-Clara path slug
    assert austin["workplace"] is None
    assert geo_of(austin) == ("Austin", "Texas", "US", "parsed")        # from locationsText "Austin, Texas"
    assert remote["workplace"] == "remote"


@pytest.mark.parametrize("slug, expected", [
    ("US-CA-Santa-Clara", {"country": "US", "region": "CA", "city": "Santa Clara"}),
    ("CA-ON-Toronto", {"country": "CA", "region": "ON", "city": "Toronto"}),
    ("GB-London", {"country": "GB", "city": "London"}),
    ("CA-San-Jose", {}),          # Canada or California? not guessed
    ("IN-Bangalore", {}),         # IN is also Indiana
    ("Austin", {}),
    ("XX-YY-Nowhere", {}),
    ("US-CA-Remote", {"country": "US", "region": "CA"}),          # found live: 'Remote' is not a city
    ("US-NY-Remote-Hybrid", {"country": "US", "region": "NY"}),
    ("Israel-Tel-Aviv", {"country": "IL", "city": "Tel Aviv"}),   # Nvidia-style: country name first
    ("United-Kingdom-London", {"country": "GB", "city": "London"}),
    ("Germany-Remote", {"country": "DE"}),
    ("China-Shanghai", {"country": "CN", "city": "Shanghai"}),
    ("Jersey-City", {}),                                          # not the Channel Island
    ("Panama-City", {}),                                          # Florida, not Panama
    ("Georgia-Tbilisi", {}),
    ("Remote", {}),
])
def test_workday_slug_geo(slug, expected):
    assert workday._slug_geo({"externalPath": f"/job/{slug}/Title_R1"}) == expected
    assert workday._slug_geo({"externalPath": "/other/x"}) == {}


def test_smartrecruiters_fields(fake_fetch):
    fake_fetch(smartrecruiters, {"/companies/acme/postings": load_fixture("smartrecruiters_postings.json")})
    dublin, writer = smartrecruiters.fetch_jobs(CTX, Detection("smartrecruiters", "acme"), SITE)
    assert dublin["workplace"] == "onsite" and dublin["remote"] is False
    assert dublin["employment_type"] == "full_time"
    assert geo_of(dublin) == ("Dublin", "Leinster", "IE", "feed")
    assert writer["workplace"] == "remote" and writer["employment_type"] == "contract"
    assert writer["country"] == "US"


def test_smartrecruiters_hybrid_flag():
    assert smartrecruiters._workplace({"hybrid": True, "remote": False}) == "hybrid"
    assert smartrecruiters._workplace({"city": "X"}) is None


# ---------- generic JSON-LD ----------

def page_with(posting):
    html = f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head></html>'
    return {"url": "https://acme.com/jobs/1", "final_url": "https://acme.com/jobs/1", "status": 200, "html": html}


def test_json_ld_structured_address_and_employment_type():
    page = page_with({
        "@type": "JobPosting", "title": "Data Engineer", "url": "https://acme.com/jobs/1",
        "employmentType": "FULL_TIME", "datePosted": "2025-03-01",
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Berlin", "addressRegion": "BE", "addressCountry": "DE"}},
    })
    (posting,) = generic.job_postings_in(page)
    job = generic.job_from_ld(posting, page["final_url"], SITE)
    assert job["employment_type"] == "full_time"
    assert job["city"] == "Berlin" and job["country"] == "DE" and job["geo_confidence"] == "feed"


def test_json_ld_telecommute_is_remote_and_country_object_is_read():
    page = page_with({
        "@type": "JobPosting", "title": "Support", "jobLocationType": "TELECOMMUTE", "employmentType": ["PART_TIME", "CONTRACTOR"],
        "jobLocation": {"address": {"addressCountry": {"@type": "Country", "name": "United States"}}},
    })
    (posting,) = generic.job_postings_in(page)
    job = generic.job_from_ld(posting, page["final_url"], SITE)
    assert job["workplace"] == "remote" and job["remote"] is True
    assert job["employment_type"] == "part_time"                          # first listed
    assert job["country"] == "US"


def test_json_ld_without_structure_falls_back_to_text():
    page = page_with({"@type": "JobPosting", "title": "Engineer", "jobLocation": {"address": "Austin, TX"}})
    (posting,) = generic.job_postings_in(page)
    job = generic.job_from_ld(posting, page["final_url"], SITE)
    assert (job["city"], job["country"], job["geo_confidence"]) == ("Austin", "US", "parsed")
