"""The Oracle Recruiting Cloud reader, tested against answers recorded from the live JPMorgan Chase and Uber sites
on 2026-09-30 (tests/fixtures/oracle_*.json, trimmed to a few postings)."""
import copy

import pytest

from knowitall import ats_detect, scraper
from knowitall.ats import oracle
from knowitall.ats_detect import Detection
from knowitall.context import RunConfig, ScanContext
from knowitall.discovery import Site, parse_site
from tests.conftest import load_fixture

SITE = Site(input_url="https://chase.com", root_url="https://chase.com/", host="chase.com", domain="chase.com",
            slug="chase", name="Chase")
JPMC = Detection("oracle", "jpmc.fa.oraclecloud.com/CX_1001", {"host": "jpmc.fa.oraclecloud.com", "lang": "en", "site": "CX_1001"})
UBER = Detection("oracle", "iaziqy.fa.ocs.oraclecloud.com/UberCareers",
                 {"host": "iaziqy.fa.ocs.oraclecloud.com", "lang": "en", "site": "UberCareers"})


def answer(total, rows):
    return {"items": [{"TotalJobsCount": total, "requisitionList": rows}], "count": 1, "hasMore": False, "limit": 200, "offset": 0}


def synthetic(count, template, start=0):
    rows = []
    for n in range(start, start + count):
        row = copy.deepcopy(template)
        row["Id"], row["Title"] = str(100000 + n), f"Role {n}"
        rows.append(row)
    return rows


@pytest.fixture
def board(monkeypatch):
    """Serve a paged Oracle board: board.total postings, `limit` per page, recorded requests in board.offsets."""
    template = load_fixture("oracle_jpmc.json")["items"][0]["requisitionList"][0]
    state = {"total": 120, "offsets": [], "fail": set(), "raw": None}

    def respond(url):
        offset = int(url.split("offset=")[1].split(",")[0])
        state["offsets"].append(offset)
        if state["raw"] is not None:
            return state["raw"]
        if offset in state["fail"]:
            return {"status": 500, "data": None}
        count = max(0, min(oracle.PAGE_SIZE, state["total"] - offset))
        return {"status": 200, "data": answer(state["total"], synthetic(count, template, offset))}

    monkeypatch.setattr(oracle, "fetch_json", lambda ctx, url, method="GET", json=None: respond(url))
    monkeypatch.setattr(oracle, "fetch_json_many", lambda ctx, specs, parallel=4: [respond(s["url"]) for s in specs])
    return type("Board", (), {"state": state})


# ---------- mapping real postings ----------

def test_a_real_jpmorgan_posting_maps_to_the_common_schema(monkeypatch):
    fixture = load_fixture("oracle_jpmc.json")
    monkeypatch.setattr(oracle, "fetch_json", lambda ctx, url, method="GET", json=None: {"status": 200, "data": fixture})
    jobs = oracle.fetch_jobs(ScanContext(config=RunConfig(max_jobs=4)), JPMC, SITE)
    first = jobs[0]
    assert first["title"] == "Senior Home Lending Advisor - Encinitas, CA"
    assert first["url"] == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210606635"
    assert first["location"].startswith("San Diego, CA, United States | Encinitas, CA, United States")
    assert first["department"] == "Originations"
    assert first["posted"].startswith("2026-09-30")
    assert (first["city"], first["country"]) == ("San Diego", "US")
    assert first["source"] == "oracle" and first["company"] == "Chase"


def test_a_real_uber_posting_maps_to_the_common_schema(monkeypatch):
    fixture = load_fixture("oracle_uber.json")
    monkeypatch.setattr(oracle, "fetch_json", lambda ctx, url, method="GET", json=None: {"status": 200, "data": fixture})
    jobs = oracle.fetch_jobs(ScanContext(config=RunConfig(max_jobs=4)), UBER, SITE)
    first = jobs[0]
    assert first["title"] == "Acquisition Account Executive, Uber Eats SMB"
    assert first["url"] == "https://iaziqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/UberCareers/job/152551"
    assert first["location"] == "Tokyo, Japan" and first["country"] == "JP"
    assert [j["title"] for j in jobs][2] == "エンタープライズ営業部長"         # non-Latin titles survive


def test_workplace_type_and_employment_type_are_read_when_present():
    row = {"Id": "1", "Title": "Nurse", "PostedDate": "2026-09-01", "PrimaryLocation": "Austin, TX, United States",
           "PrimaryLocationCountry": "US", "WorkplaceType": "Hybrid", "JobSchedule": "Full time", "JobFamily": None,
           "JobFunction": "Clinical", "secondaryLocations": [], "workLocation": []}
    job = oracle._job(JPMC, SITE, row)
    assert job["workplace"] == "hybrid" and job["employment_type"] == "full_time" and job["department"] == "Clinical"


def test_a_blank_workplace_type_is_unknown_not_a_guess():
    row = {"Id": "1", "Title": "Nurse", "WorkplaceType": "", "PrimaryLocation": "Austin", "secondaryLocations": [], "workLocation": []}
    assert oracle._job(JPMC, SITE, row)["workplace"] is None


# ---------- paging ----------

def test_every_page_is_read_and_streamed_as_it_arrives(board):
    batches, phases = [], []
    ctx = ScanContext(on_jobs=lambda jobs: batches.append(len(jobs)))
    ctx.phase_sink = phases.append
    jobs = oracle.fetch_jobs(ctx, JPMC, SITE)
    assert len(jobs) == 120 and batches == [50, 50, 20]
    assert sorted(board.state["offsets"]) == [0, 50, 100]
    assert phases[-1] == "Reading Oracle Recruiting jobs 120/120…"
    assert not ctx.truncated


def test_a_large_board_is_capped_at_max_jobs_and_marked_partial(board):
    board.state["total"] = 7343
    ctx = ScanContext(config=RunConfig(max_jobs=120))
    jobs = oracle.fetch_jobs(ctx, JPMC, SITE)
    assert len(jobs) == 120 and "7343" in ctx.truncated


def test_a_page_that_fails_makes_the_scan_partial_so_nothing_is_marked_closed(board):
    board.state["fail"] = {50}
    ctx = ScanContext()
    jobs = oracle.fetch_jobs(ctx, JPMC, SITE)
    assert len(jobs) == 70 and ctx.truncated


def test_stop_ends_paging_between_batches(board):
    board.state["total"] = 1000
    ctx = ScanContext()
    ctx.cancel.cancel()
    jobs = oracle.fetch_jobs(ctx, JPMC, SITE)
    assert len(jobs) == 50                                    # the first page only; no more requests were made
    assert board.state["offsets"] == [0]


def test_an_answer_that_is_not_an_oracle_search_is_not_a_board(board):
    for raw in ({"status": 404, "data": None}, {"status": 200, "data": {"items": []}}, {"status": 200, "data": "<html>"},
                {"status": 200, "data": {"items": [{"nothing": 1}]}}, {"status": 0, "data": None, "error": "host does not resolve"}):
        board.state["raw"] = raw
        assert oracle.fetch_jobs(ScanContext(), JPMC, SITE) is None


def test_an_empty_board_is_a_board_with_no_postings(board):
    board.state["total"] = 0
    assert oracle.fetch_jobs(ScanContext(), JPMC, SITE) == []


# ---------- detection ----------

def test_the_board_is_found_in_careers_page_links():
    html = ('<a href="https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword=CIB&amp;mode=location">x</a>'
            '<a href="https://iaziqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/UberCareers/my-profile/sign-in">y</a>')
    detections, unsupported, _ = ats_detect.detect([{"url": "u", "final_url": "u", "html": html}], parse_site("chase.com"))
    tokens = sorted(d.token for d in detections)
    assert tokens == ["iaziqy.fa.ocs.oraclecloud.com/UberCareers", "jpmc.fa.oraclecloud.com/CX_1001"]
    assert detections[0].extra["lang"] == "en" and "oracle" not in unsupported


def test_a_locale_with_a_region_is_kept():
    html = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en-GB/sites/CX_2/jobs"
    detections, _, _ = ats_detect.detect([{"url": "u", "final_url": "u", "html": html}], parse_site("acme.com"))
    assert detections[0].extra == {"host": "eeho.fa.us2.oraclecloud.com", "lang": "en-GB", "site": "CX_2"}


def test_the_pipeline_reads_an_oracle_board_end_to_end(board, monkeypatch):
    from knowitall import discovery, generic
    page = lambda url, html="": {"url": url, "final_url": url, "status": 200 if html else 404, "html": html, "error": None}
    nav = "".join(f'<a href="/p{i}">p</a>' for i in range(12))
    careers = ('<html><title>Careers</title><a href="https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs">'
               f'Search jobs</a>{nav}</html>')
    pages = {"https://chase.com/": page("https://chase.com/", f'<a href="/careers">Careers</a>{nav}'),
             "https://chase.com/careers": page("https://chase.com/careers", careers)}
    get = lambda url: pages.get(url) or page(url)
    for module in (discovery, scraper, generic):
        for name in ("fetch_pages",):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda ctx, urls, parallel=8: [get(u) for u in urls])
    monkeypatch.setattr(discovery, "fetch_page", lambda ctx, url: get(url))
    monkeypatch.setattr(discovery, "_sitemap_urls", lambda ctx, site: [])
    monkeypatch.setattr(scraper, "render_page", lambda *a, **k: pytest.fail("rendered: the Oracle board should have been read"))
    result = scraper.find_jobs(ScanContext(config=RunConfig(use_browser=False)), "chase.com")
    assert result["outcome"] == "found" and len(result["jobs"]) == 120
    assert result["source"] == "oracle" and "jpmc.fa.oraclecloud.com/CX_1001" in result["source_detail"]
