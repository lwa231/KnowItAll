"""Finding careers pages on other domains (chase.com -> careers.jpmorgan.com) and recognising job links
(Tesla- and Uber-shaped URLs), plus the URL identity of postings that have no page of their own."""
import pytest

from knowitall import discovery, generic
from knowitall.context import ScanContext
from knowitall.discovery import Site, discover, parse_site
from knowitall.normalize import url_key

SITE = Site(input_url="https://acme.com", root_url="https://acme.com/", host="acme.com",
            domain="acme.com", slug="acme", name="Acme")
NAV = "".join(f'<a href="/page{i}">Page {i}</a>' for i in range(12))


def page(url, html="", status=200, final_url=None):
    return {"url": url, "final_url": final_url or url, "status": status, "html": html, "error": None}


@pytest.fixture
def web(monkeypatch):
    pages = {}
    get = lambda url: pages.get(url) or page(url, "", 404)
    monkeypatch.setattr(discovery, "fetch_page", lambda ctx, url: get(url))
    monkeypatch.setattr(discovery, "fetch_pages", lambda ctx, urls, parallel=8: [get(u) for u in urls])
    monkeypatch.setattr(discovery, "render_page", lambda ctx, url, **k: get(url))
    monkeypatch.setattr(discovery, "_sitemap_urls", lambda ctx, site: [])
    return lambda url, html="", status=200, final_url=None: pages.__setitem__(url, page(url, html, status, final_url))


# ---------- 1D: careers on another domain ----------

CHASE_LISTING = ("<html><head><title>Careers at Chase | JPMorgan Chase</title></head><body>"
                 + "".join(f'<a href="/us/en/jobs/21000{n}/analyst-{n}">Analyst {n}</a>' for n in range(5)) + "</body></html>")


def test_a_careers_link_to_a_parent_company_domain_becomes_a_candidate(web):
    site = parse_site("chase.com")
    web("https://chase.com/", f'<html><body><a href="https://careers.jpmorgan.com/US/en/chase">Careers</a>{NAV}</body></html>')
    web("https://careers.jpmorgan.com/US/en/chase", CHASE_LISTING)
    home, candidates = discover(ScanContext(), site)
    assert [c["final_url"] for c in candidates] == ["https://careers.jpmorgan.com/US/en/chase"]
    assert candidates[0]["hosted_elsewhere"] == "careers.jpmorgan.com"
    assert site.careers_domains == {"chase.com", "jpmorgan.com"}


def test_job_links_on_the_sister_domain_are_accepted_but_not_before_it_is_known():
    site = parse_site("chase.com")
    listing = page("https://careers.jpmorgan.com/US/en/chase", CHASE_LISTING)
    assert generic.job_links(listing, site) == []                      # a stranger's domain: not this company's
    site.careers_domains.add("jpmorgan.com")
    assert len(generic.job_links(listing, site)) == 5


def test_a_careers_probe_that_redirects_to_another_domain_is_followed(web):
    site = parse_site("acme.com")
    web("https://acme.com/", f"<html><body>{NAV}</body></html>")
    web("https://acme.com/careers", CHASE_LISTING, final_url="https://jobs.partner.io/acme/careers")
    _, candidates = discover(ScanContext(), site)
    assert candidates and candidates[0]["hosted_elsewhere"] == "jobs.partner.io"
    assert "partner.io" in site.careers_domains


def test_an_offsite_link_that_does_not_say_careers_is_not_followed(web):
    site = parse_site("acme.com")
    web("https://acme.com/", f'<html><body><a href="https://blog.other.com/careers-advice">Read our blog</a>{NAV}</body></html>')
    web("https://blog.other.com/careers-advice", "<html><title>Careers advice</title></html>")
    _, candidates = discover(ScanContext(), site)
    assert candidates == [] and site.careers_domains == {"acme.com"}


def test_an_offsite_page_that_does_not_look_like_careers_is_dropped(web):
    site = parse_site("acme.com")
    web("https://acme.com/", f'<html><body><a href="https://www.elsewhere.com/team">Careers</a>{NAV}</body></html>')
    web("https://www.elsewhere.com/team", "<html><title>Our team</title></html>")
    _, candidates = discover(ScanContext(), site)
    assert candidates == []


@pytest.mark.parametrize("link,board", [("https://www.linkedin.com/company/acme/jobs", "LinkedIn"),
                                        ("https://www.indeed.com/cmp/Acme/jobs", "Indeed")])
def test_job_boards_are_never_followed_and_are_noted(web, link, board):
    site = parse_site("acme.com")
    web("https://acme.com/", f'<html><body><a href="{link}">Careers</a>{NAV}</body></html>')
    web(link, "<html><title>Acme Jobs</title></html>")
    ctx = ScanContext()
    _, candidates = discover(ctx, site)
    assert candidates == [] and ctx.noted("job_board") == [board]
    assert site.careers_domains == {"acme.com"}


def test_social_sites_are_ignored_without_a_note(web):
    site = parse_site("acme.com")
    web("https://acme.com/", f'<html><body><a href="https://twitter.com/acme">Careers</a>{NAV}</body></html>')
    ctx = ScanContext()
    _, candidates = discover(ctx, site)
    assert candidates == [] and ctx.noted("job_board") == []


def test_registrable_domains():
    assert discovery.registrable_domain("careers.jpmorgan.com") == "jpmorgan.com"
    assert discovery.registrable_domain("jobs.example.co.uk") == "example.co.uk"


# ---------- 1E: job links and locale ----------

@pytest.mark.parametrize("path,expected", [
    ("/careers/search/job/software-engineer-225712", True),       # Tesla: the first "/careers/search" is navigation
    ("/careers/search/", False),
    ("/global/en/careers/list/135592/", True),                    # Uber
    ("/global/en/careers/list/?location=USA", False),
    ("/global/en/careers/list/", False),
    ("/careers/positions/senior-engineer", True),                 # unchanged
    ("/careers/teams/engineering", False),                        # unchanged: navigation
    ("/careers/benefits", False),
    ("/en/jobs/12345", True),
])
def test_job_link_families(path, expected):
    listing = "https://acme.com/careers/"
    assert generic._is_job_link("https://acme.com" + path, SITE, listing) is expected


def test_a_query_id_marks_a_job_even_on_a_navigation_path():
    assert generic._is_job_link("https://acme.com/careers/search?jobid=101", SITE, "https://acme.com/careers/")


def test_tesla_shaped_links_on_a_listing_page_are_all_found():
    html = "".join(f'<a href="/careers/search/job/engineer-{n}{n}{n}{n}{n}">Engineer {n}</a>' for n in range(1, 6))
    found = generic.job_links(page("https://acme.com/careers/search", html), SITE)
    assert len(found) == 5


def test_count_job_links_matches_job_links_without_parsing():
    html = "".join(f'<a href="/careers/search/job/engineer-{n}{n}{n}{n}">E</a>' for n in range(1, 6)) + '<a href="/about">About</a>'
    assert generic.count_job_links(html, "https://acme.com/careers/search", SITE) == 5


def test_a_us_en_prefix_is_english_and_no_longer_penalised():
    score = lambda path: discovery._final_score(page(f"https://acme.com{path}"), 0)
    assert score("/us/en/careers/") >= score("/careers/") - 6         # the plan's bar: only the per-segment cost
    assert score("/en/careers/") >= score("/careers/") - 3
    assert score("/de/careers/") < score("/careers/") - 30           # a non-English locale still loses
    assert score("/fr-ca/careers/") < score("/careers/") - 30
    assert discovery.EXACT_CAREERS_PATH_RE.match("/us/en/careers/")
    assert discovery.EXACT_CAREERS_PATH_RE.match("/en-gb/jobs")


# ---------- postings without a URL of their own ----------

def ld(title, identifier=None, url=None):
    import json
    body = {"@context": "https://schema.org", "@type": "JobPosting", "title": title}
    if identifier:
        body["identifier"] = {"@type": "PropertyValue", "value": identifier}
    if url:
        body["url"] = url
    return f'<script type="application/ld+json">{json.dumps(body)}</script>'


def test_five_embedded_postings_without_urls_stay_five():
    html = "<html><body>" + "".join(ld(f"Engineer {n}") for n in range(5)) + "</body></html>"
    jobs = generic.extract_jobs(ScanContext(), [page("https://acme.com/careers", html)], SITE)
    urls = [j["url"] for j in jobs]
    assert len(urls) == 5 and len({url_key(u) for u in urls}) == 5
    assert all(u.startswith("https://acme.com/careers#job-") for u in urls)


def test_the_identifier_wins_over_the_title_and_duplicates_are_told_apart():
    html = "<body>" + ld("Engineer", "R-77") + ld("Engineer") + ld("Engineer") + "</body>"
    jobs = generic.extract_jobs(ScanContext(), [page("https://acme.com/careers", html)], SITE)
    assert [j["url"] for j in jobs] == ["https://acme.com/careers#job-r-77", "https://acme.com/careers#job-engineer",
                                       "https://acme.com/careers#job-engineer-2"]


def test_a_posting_with_its_own_url_keeps_it():
    html = "<body>" + ld("Engineer", url="/jobs/eng-1") + "</body>"
    jobs = generic.extract_jobs(ScanContext(), [page("https://acme.com/careers", html)], SITE)
    assert jobs[0]["url"] == "https://acme.com/jobs/eng-1"


def test_url_key_keeps_only_job_fragments():
    assert url_key("https://www.acme.com/careers/#job-R-77") == "acme.com/careers#job-R-77"
    assert url_key("https://acme.com/careers#section-2") == "acme.com/careers"
    assert url_key("https://acme.com/careers#job-1") != url_key("https://acme.com/careers#job-2")
