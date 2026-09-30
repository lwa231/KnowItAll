"""Every company scan ends in exactly one explicit outcome (knowitall/outcomes.py), with wording for it."""
import time

import pytest

from knowitall import discovery, fetch, generic, outcomes, runner as runner_module, scraper, store
from knowitall.context import CancelToken, RunConfig, ScanContext
from knowitall.discovery import Site
from knowitall.normalize import make_job
from knowitall.runner import Runner

CHALLENGE = ("<html><head><title>Access Denied</title></head><body>You don't have permission to access this page."
             " Reference #18.4b1e1102.1712345678.1a2b3c <a href='https://errors.edgesuite.net/18.4b1e1102'>x</a>"
             "</body></html>")
NAV = "".join(f'<a href="/page{i}">Page {i}</a>' for i in range(12))       # enough links to look like a real page


def page(url, html="", status=200, error=None, final_url=None):
    return {"url": url, "final_url": final_url or url, "status": status, "html": html, "error": error}


@pytest.fixture
def web(monkeypatch):
    """A tiny fake internet: web.set(url, html, status). Unknown URLs are 404. Chrome renders whatever `render` says."""
    pages, state = {}, {"render": {}, "renders": []}

    def get(url):
        return pages.get(url) or page(url, "", 404)

    def fetch_page(ctx, url):
        return get(url)

    def fetch_many(ctx, urls, parallel=8):
        return [get(u) for u in urls]

    def render(ctx, url, mode="page", count_links=None):
        state["renders"].append((url, mode))
        return state["render"].get(url) or get(url)

    for module, names in ((discovery, ("fetch_page", "fetch_pages", "render_page")),
                          (scraper, ("fetch_pages", "render_page")), (generic, ("fetch_pages",))):
        for name, fake in (("fetch_page", fetch_page), ("fetch_pages", fetch_many), ("render_page", render)):
            if name in names:
                monkeypatch.setattr(module, name, fake)
    monkeypatch.setattr(discovery, "_sitemap_urls", lambda ctx, site: [])
    monkeypatch.setattr(fetch, "host_resolves", lambda host: True)

    class Web:
        renders = state["renders"]

        @staticmethod
        def set(url, html="", status=200, final_url=None, error=None):
            pages[url] = page(url, html, status, error, final_url)

        @staticmethod
        def render(url, **fields):
            state["render"][url] = page(url, **fields)
    return Web


def scan(url="acme.com", **config):
    return scraper.find_jobs(ScanContext(config=RunConfig(**config)), url)


# ---------- the wording ----------

def test_every_outcome_has_words():
    for outcome in outcomes.OUTCOMES:
        message, _ = outcomes.describe(outcome, "acme.com", count=3, platform="taleo", limit=180)
        assert message and message != outcome


def test_the_wording_matches_the_plan():
    assert outcomes.describe("no_listings", "acme.com")[0] == "No listings available on this page."
    assert outcomes.describe("no_careers_page", "acme.com")[0] == "No careers page found on acme.com."
    assert outcomes.describe("unreachable", "acme.com")[0] == "Couldn't reach acme.com."
    assert outcomes.describe("blocked", "acme.com")[0] == "acme.com blocked automated access."
    assert outcomes.describe("unsupported", "acme.com", platform="icims")[0] == \
        "acme.com uses iCIMS, which KnowItAll can't read yet."
    assert outcomes.describe("timed_out", "acme.com", count=12, limit=180)[0] == \
        "Stopped after 3 min — this site is slow. 12 postings found."
    assert outcomes.describe("stopped", "acme.com", count=1)[0] == "Stopped. 1 posting found."
    assert outcomes.describe("error", "acme.com")[1] == "Details are in the log."
    assert outcomes.describe("no_listings", "acme.com", board="LinkedIn")[0] == \
        "acme.com lists its jobs on LinkedIn, which KnowItAll doesn't scan."


# ---------- one fixture per outcome ----------

def test_unreachable_home(web):
    web.set("https://acme.com/", error="host does not resolve", status=0)
    result = scan()
    assert result["outcome"] == "unreachable" and result["outcome_detail"] == "Couldn't reach acme.com."
    assert result["careers_url"] is None


def test_home_that_blocks_us_even_in_chrome(web):
    web.set("https://acme.com/", CHALLENGE, status=403)
    web.render("https://acme.com/", html=CHALLENGE, status=403)
    result = scan(use_browser=True, renderer=lambda url, cancel: None)
    assert result["outcome"] == "blocked"
    assert result["careers_url"] == "https://acme.com/"
    assert "blocked automated access" in result["outcome_detail"]


def test_careers_page_that_stays_blocked_is_blocked_with_a_link_to_it(web):
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", CHALLENGE, status=403)
    result = scan(use_browser=True, renderer=lambda url, cancel: None)
    assert result["outcome"] == "blocked" and result["careers_url"] == "https://acme.com/careers"


def test_no_candidates_means_no_careers_page(web):
    web.set("https://acme.com/", f"<html><title>Acme</title>{NAV}</html>")
    result = scan()
    assert result["outcome"] == "no_careers_page"
    assert result["outcome_detail"] == "No careers page found on acme.com."
    assert "no careers page found" in result["notes"]


def test_unsupported_platform_links_to_the_vendors_page(web):
    web.set("https://acme.com/", f'<a href="https://acme.taleo.net/careersection/ex/jobsearch.ftl">Careers</a>{NAV}')
    result = scan()
    assert result["outcome"] == "unsupported"
    assert result["outcome_detail"] == "acme.com uses Taleo, which KnowItAll can't read yet."
    assert result["careers_url"] == "https://acme.taleo.net/careersection/ex/jobsearch.ftl"


def test_an_oracle_host_without_a_readable_site_is_still_unsupported(web):
    """The Oracle reader needs the site in the URL; a bare pod address only tells us the vendor."""
    html = ('<a href="https://eeho.fa.us2.oraclecloud.com/">Careers</a>' + NAV)
    web.set("https://acme.com/", html)
    result = scan()
    assert result["outcome"] == "unsupported" and "Oracle Recruiting" in result["outcome_detail"]
    assert result["careers_url"].startswith("https://eeho.fa.us2.oraclecloud.com/")


def test_careers_page_without_postings_is_no_listings_with_the_page_to_open(web):
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", f"<html><title>Careers at Acme</title>{NAV}<p>We are hiring.</p></html>")
    result = scan()
    assert result["outcome"] == "no_listings"
    assert result["outcome_detail"] == "No listings available on this page."
    assert result["outcome_hint"] == "KnowItAll found acme.com's careers page but couldn't read any job postings from it."
    assert result["careers_url"] == "https://acme.com/careers"


def test_a_company_that_only_links_to_linkedin_says_so(web):
    web.set("https://acme.com/", f'<a href="https://www.linkedin.com/company/acme/jobs">Careers</a>{NAV}')
    web.set("https://www.linkedin.com/company/acme/jobs", "<html><title>Acme Jobs | LinkedIn</title></html>")
    result = scan()
    assert result["outcome"] == "no_listings"
    assert result["outcome_detail"] == "acme.com lists its jobs on LinkedIn, which KnowItAll doesn't scan."
    assert result["careers_url"] is None


def test_postings_found(web, monkeypatch):
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", f"<html><title>Careers</title>{NAV}</html>")
    monkeypatch.setattr(generic, "extract_jobs", lambda ctx, pages, site: (
        ctx.emit([make_job("Acme", f"Job {n}", f"https://acme.com/jobs/job-{n}") for n in range(3)]), [])[1])
    result = scan()
    assert result["outcome"] == "found" and result["outcome_detail"] == "3 postings"


def test_stopped_scan_reports_stopped_with_what_it_found(web, monkeypatch):
    token = CancelToken()
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", f"<html><title>Careers</title>{NAV}</html>")

    def extract(ctx, pages, site):
        ctx.emit([make_job("Acme", "One", "https://acme.com/jobs/one")])
        token.cancel()
        return []
    monkeypatch.setattr(generic, "extract_jobs", extract)
    result = scraper.find_jobs(ScanContext(cancel=token), "acme.com")
    assert result["outcome"] == "stopped" and result["outcome_detail"] == "Stopped. 1 posting found."


def test_time_limit_cancel_reason_makes_it_timed_out(web, monkeypatch):
    token = CancelToken()
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", f"<html><title>Careers</title>{NAV}</html>")
    monkeypatch.setattr(generic, "extract_jobs", lambda ctx, pages, site: (token.cancel("time_limit"), [])[1])
    result = scraper.find_jobs(ScanContext(config=RunConfig(time_limit=180), cancel=token), "acme.com")
    assert result["outcome"] == "timed_out"
    assert result["outcome_detail"] == "Stopped after 3 min — this site is slow. 0 postings found."


def test_the_cancel_token_remembers_why():
    parent = CancelToken()
    child = parent.child()
    assert child.reason is None
    child.cancel("time_limit")
    child.cancel("stop")                                    # the first reason sticks
    assert child.reason == "time_limit" and parent.reason is None
    other = parent.child()
    parent.cancel()
    assert other.reason == "stop"


# ---------- through the Runner ----------

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    store.init()
    yield store
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


def run_one(monkeypatch, behaviour, options=None):
    monkeypatch.setattr(runner_module, "find_jobs", lambda ctx, url, on_jobs=None: behaviour(ctx, url, on_jobs))
    runner = Runner()
    runner.start(["acme.com"], {"autosave": False, "history": False, **(options or {})})
    assert runner.wait(10)
    return runner, runner.snapshot()["companies"][0]


def test_an_unexpected_exception_is_the_error_outcome(monkeypatch):
    def boom(ctx, url, on_jobs):
        raise RuntimeError("kaput")
    _, company = run_one(monkeypatch, boom)
    assert company["state"] == "failed" and company["outcome"] == "error"
    assert company["outcome_detail"] == "Something went wrong scanning acme.com."
    assert company["outcome_hint"] == "Details are in the log."


def test_the_runner_stores_the_scans_outcome_and_careers_link(monkeypatch):
    def scan_result(ctx, url, on_jobs):
        return {"domain": "acme.com", "jobs": [], "notes": [], "stopped": False, "outcome": "no_listings",
                "outcome_detail": "No listings available on this page.", "outcome_hint": "hint",
                "careers_url": "https://acme.com/careers", "careers_note": "Careers for acme.com are hosted on x.com."}
    _, company = run_one(monkeypatch, scan_result)
    assert (company["state"], company["outcome"]) == ("done", "no_listings")
    assert company["careers_url"] == "https://acme.com/careers" and company["phase"] is None
    assert company["careers_note"].endswith("hosted on x.com.")


def test_a_company_that_outlives_its_time_limit_is_cut_off_and_keeps_its_rows(monkeypatch):
    def slow(ctx, url, on_jobs):
        on_jobs([make_job("Acme", "Early", "https://acme.com/jobs/early")])
        while not ctx.should_stop():
            time.sleep(0.01)
        return {"domain": "acme.com", "jobs": [], "notes": [], "stopped": True}
    started = time.time()
    _, company = run_one(monkeypatch, slow, {"time_limit": 0.3})
    assert time.time() - started < 3
    assert company["state"] == "stopped" and company["outcome"] == "timed_out"
    assert company["jobs_count"] == 1
    assert company["outcome_detail"].startswith("Stopped after") and company["outcome_detail"].endswith("this site is slow. 1 posting found.")


def test_pressing_stop_is_the_stopped_outcome_not_timed_out(monkeypatch):
    def slow(ctx, url, on_jobs):
        while not ctx.should_stop():
            time.sleep(0.01)
        return {"domain": "acme.com", "jobs": [], "notes": [], "stopped": True}
    monkeypatch.setattr(runner_module, "find_jobs", lambda ctx, url, on_jobs=None: slow(ctx, url, on_jobs))
    runner = Runner()
    runner.start(["acme.com"], {"autosave": False, "history": False})
    time.sleep(0.1)
    runner.stop()
    assert runner.wait(5)
    company = runner.snapshot()["companies"][0]
    assert company["outcome"] == "stopped" and company["outcome_detail"] == "Stopped. 0 postings found."


def test_phases_arrive_in_order_and_clear_when_the_scan_ends(monkeypatch):
    def phased(ctx, url, on_jobs):
        for text in ("Finding the careers page…", "Reading the Greenhouse job board…"):
            ctx.phase(text)
        return {"domain": "acme.com", "jobs": [], "notes": [], "stopped": False}
    runner, company = run_one(monkeypatch, phased)
    events, _ = runner.events_since(0)
    assert [e["text"] for e in events if e["kind"] == "phase"] == ["Finding the careers page…", "Reading the Greenhouse job board…"]
    assert all(e["domain"] == "acme.com" for e in events if e["kind"] == "phase")
    assert company["phase"] is None


def test_time_limit_setting_accepts_only_the_offered_choices():
    from knowitall import settings
    assert settings.DEFAULTS["time_limit_min"] == 3
    assert settings.validate({"time_limit_min": 5}) == {"time_limit_min": 5}
    for bad in (2, 0, "3", True, 10):
        with pytest.raises(ValueError):
            settings.validate({"time_limit_min": bad})
    assert settings.clean({"time_limit_min": 7})["time_limit_min"] == 3


def test_the_vendor_link_is_one_clean_url_even_inside_a_list_of_urls():
    from knowitall.ats_detect import vendor_link
    blob = ('{"allowList": ["https://jpmc.fa.oraclecloud.com/", "https://chasebonus.com", '
            '"https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs"]}')
    assert vendor_link([{"html": blob}], "oracle") == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs"
    assert vendor_link([{"html": '<a href="https://acme.taleo.net/x">a</a>'}], "taleo") == "https://acme.taleo.net/x"
    assert vendor_link([{"html": "nothing"}], "taleo") is None


def test_links_that_only_look_like_postings_are_not_trusted_next_to_an_unsupported_platform(web):
    nav = "".join(f'<a href="/careers/{slug}">{title}</a>' for slug, title in (
        ("work-with-us", "Explore our culture"), ("grow-with-us", "Our people"), ("teams-and-programs", "Programs for you")))
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", f'<html><title>Careers</title>{nav}'
            '<a href="https://acme.taleo.net/careersection/ex/jobsearch.ftl">Search jobs</a></html>')
    for slug in ("work-with-us", "grow-with-us", "teams-and-programs"):
        web.set(f"https://acme.com/careers/{slug}", "<html><title>A page</title></html>")
    result = scan()
    assert result["jobs"] == [] and result["outcome"] == "unsupported"


def test_a_supported_page_with_the_same_links_still_shows_them(web):
    """Without an unsupported platform in sight, the same heuristic rows are kept: the guard is targeted."""
    nav = "".join(f'<a href="/careers/{slug}">{title}</a>' for slug, title in (
        ("senior-engineer", "Senior Engineer"), ("staff-designer", "Staff Designer"), ("data-analyst", "Data Analyst")))
    web.set("https://acme.com/", f'<a href="/careers">Careers</a>{NAV}')
    web.set("https://acme.com/careers", f"<html><title>Careers</title>{nav}</html>")
    for slug in ("senior-engineer", "staff-designer", "data-analyst"):
        web.set(f"https://acme.com/careers/{slug}", "<html><title>A job</title></html>")
    result = scan()
    assert result["outcome"] == "found" and len(result["jobs"]) == 3


def test_section_links_are_not_postings():
    from knowitall import generic
    site = discovery.parse_site("acme.com")
    html = "".join(f'<a href="/careers/{s}">{t}</a>' for s, t in (
        ("work-with-us", "Work with us"), ("explore-opportunities", "Explore opportunities"),
        ("students", "Students and graduates"), ("graduate-software-engineer", "Graduate Software Engineer")))
    found = generic.job_links({"final_url": "https://acme.com/careers", "html": html}, site)
    assert [link["title"] for link in found] == ["Graduate Software Engineer"]
