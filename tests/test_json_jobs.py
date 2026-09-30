"""Reading postings out of the JSON a JavaScript careers page loaded, and the browser pool's listing mode."""
import json

import pytest

from knowitall import adapters, json_jobs
from knowitall.browser import BrowserPool
from knowitall.context import ScanContext
from knowitall.discovery import Site

SITE = Site(input_url="https://acme.com", root_url="https://acme.com/", host="acme.com",
            domain="acme.com", slug="acme", name="Acme")
PAGE = "https://acme.com/global/en/careers/list/"


def payload(data, url="https://acme.com/api/loadSearchJobsResults"):
    return {"url": url, "data": data}


def uber_style(start=0, count=5):
    """results[] with id, title, location{city,country}, department: the shape of Uber's search API."""
    return {"status": "success", "data": {"results": [
        {"id": 135000 + n, "title": f"Software Engineer {n}", "department": "Engineering", "type": "Full-Time",
         "location": {"city": "San Francisco", "region": "California", "country": "USA"},
         "creationDate": "2026-09-01T00:00:00.000Z"} for n in range(start, start + count)],
        "totalResults": {"low": 40}}}


# ---------- finding the array ----------

def test_an_uber_style_result_list_becomes_postings():
    ctx = ScanContext()
    jobs = json_jobs.extract(ctx, [payload(uber_style())], PAGE, SITE)
    assert len(jobs) == 5
    first = jobs[0]
    assert first["title"] == "Software Engineer 0" and first["department"] == "Engineering"
    assert first["url"] == f"{PAGE}#job-135000"                       # no url in the data: page + #job-<id>
    assert (first["city"], first["country"]) == ("San Francisco", "US")
    assert first["source"] == "json-sniffed" and first["employment_type"] == "full_time"
    assert first["posted"].startswith("2026-09-01")
    assert first["company"] == "Acme"


def test_urls_are_used_when_the_data_has_them():
    data = {"jobs": [{"title": f"Role {n}", "id": n, "applyUrl": f"/careers/job/{n}", "location": "Austin, TX"} for n in range(4)]}
    jobs = json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)
    assert [j["url"] for j in jobs] == [f"https://acme.com/careers/job/{n}" for n in range(4)]
    absolute = {"jobs": [{"name": f"Role {n}", "url": f"https://jobs.acme.com/{n}", "location": "Austin"} for n in range(3)]}
    assert [j["url"] for j in json_jobs.extract(ScanContext(), [payload(absolute)], PAGE, SITE)] == \
        [f"https://jobs.acme.com/{n}" for n in range(3)]


def test_a_nested_state_shape_is_found():
    data = {"props": {"pageProps": {"initialState": {"search": {"hits": [
        {"jobId": f"R{n}", "jobTitle": f"Analyst {n}", "primaryLocation": "London, United Kingdom",
         "jobFamily": "Finance"} for n in range(6)]}}}}}
    jobs = json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)
    assert len(jobs) == 6 and jobs[0]["department"] == "Finance" and jobs[0]["country"] == "GB"
    assert jobs[0]["url"].endswith("#job-R0")


def test_a_list_of_filters_is_not_a_list_of_jobs():
    facets = {"facets": [{"id": code, "name": name, "count": n} for code, name, n in
                         (("US", "United States", 120), ("GB", "United Kingdom", 30), ("DE", "Germany", 12))],
              "departments": [{"id": 1, "name": "Engineering"}, {"id": 2, "name": "Sales"}, {"id": 3, "name": "Legal"}]}
    assert json_jobs.extract(ScanContext(), [payload(facets)], PAGE, SITE) == []


def test_fewer_than_three_items_is_not_a_listing():
    assert json_jobs.extract(ScanContext(), [payload(uber_style(count=2))], PAGE, SITE) == []


def test_pages_of_one_listing_are_merged_and_repeats_dropped():
    first, second = payload(uber_style(0, 5)), payload(uber_style(3, 5))         # ids 3 and 4 appear twice
    jobs = json_jobs.extract(ScanContext(), [first, second], PAGE, SITE)
    assert len(jobs) == 8 and len({j["url"] for j in jobs}) == 8


def test_the_biggest_listing_wins_when_a_page_loads_several_arrays():
    other = {"related": [{"title": f"Related {n}", "id": n, "url": f"/r/{n}"} for n in range(3)]}
    jobs = json_jobs.extract(ScanContext(), [payload(other, "https://acme.com/api/related"), payload(uber_style(0, 7))], PAGE, SITE)
    assert len(jobs) == 7 and jobs[0]["title"].startswith("Software Engineer")


def test_payloads_that_are_not_json_objects_are_ignored():
    assert json_jobs.extract(ScanContext(), [payload("text"), payload(None), payload([1, 2, 3]), payload({})], PAGE, SITE) == []


def test_a_runaway_payload_does_not_hang():
    deep = current = {}
    for _ in range(50):
        current["next"] = {}
        current = current["next"]
    assert json_jobs.find_arrays(deep) == []


def test_only_explicit_remote_true_is_kept():
    data = {"jobs": [{"title": "Remote Role", "id": 1, "isRemote": True, "location": "Anywhere"},
                     {"title": "Office Role", "id": 2, "isRemote": False, "location": "Austin, TX"},
                     {"title": "Other Role", "id": 3, "location": "Austin, TX"}]}
    jobs = json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)
    assert [j["workplace"] for j in jobs] == ["remote", None, None]


def test_flat_city_state_country_keys_give_structure():
    data = {"jobs": [{"title": f"Nurse {n}", "id": n, "city": "Austin", "state": "TX", "country": "US"} for n in range(3)]}
    jobs = json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)
    assert (jobs[0]["city"], jobs[0]["region"], jobs[0]["country"]) == ("Austin", "Texas", "US")     # "TX" is normalised


# ---------- adapters ----------

def test_a_registered_adapter_is_used_before_the_generic_reader(monkeypatch):
    marker = [{"title": "From adapter", "url": "https://acme.com/x", "company": "Acme"}]
    monkeypatch.setitem(adapters.ADAPTERS, "acme.com", lambda payloads, page_url, site: marker)
    assert json_jobs.extract(ScanContext(), [payload(uber_style())], PAGE, SITE) == marker


def test_an_adapter_that_finds_nothing_falls_back_to_the_generic_reader(monkeypatch):
    monkeypatch.setitem(adapters.ADAPTERS, "acme.com", lambda payloads, page_url, site: None)
    assert len(json_jobs.extract(ScanContext(), [payload(uber_style())], PAGE, SITE)) == 5


def test_an_adapter_is_found_by_a_careers_domain_too(monkeypatch):
    site = Site("https://chase.com", "https://chase.com/", "chase.com", "chase.com", "chase", "Chase",
                careers_domains={"jpmorgan.com"})
    monkeypatch.setitem(adapters.ADAPTERS, "jpmorgan.com", lambda *a: "adapter")
    assert adapters.for_site(site)() if False else adapters.for_site(site) is adapters.ADAPTERS["jpmorgan.com"]


def test_describe_lists_keys_and_array_sizes_for_the_debug_log():
    lines = json_jobs.describe([payload(uber_style())])
    assert len(lines) == 1
    assert "loadSearchJobsResults" in lines[0] and "keys=['status', 'data']" in lines[0] and "data.results" in lines[0]


# ---------- BrowserPool listing mode ----------

LISTING_HTML = "<html>" + "x" * 600 + "</html>"


class ListingDriver:
    """A fake Chrome whose page grows as it is scrolled or its 'load more' button is clicked."""

    def __init__(self, links_by_step=None, clicks=3, json_bodies=None):
        self.current_url = "about:blank"
        self.page_html = ""
        self.closed = False
        self.handlers = []
        self.links = 0
        self.scrolls = 0
        self.clicks_left = clicks
        self.clicks = 0
        self.initial = 0
        self.json_bodies = json_bodies or {}
        self.on_scroll = lambda: None

    # the pieces of botasaurus' Driver the pool uses
    def get(self, url, timeout=60):
        self.current_url = url
        self._render(self.initial)
        for request_id, (kind, target) in {rid: (k, u) for rid, (k, u, _) in self.json_bodies.items()}.items():
            response = type("R", (), {"mime_type": kind, "url": target})()
            for handler in self.handlers:
                handler(request_id, response, None)

    def after_response_received(self, handler):
        self.handlers.append(handler)

    def collect_response(self, request_id):
        body = self.json_bodies[request_id][2]
        return type("Body", (), {"get_decoded_content": lambda self_: body})()

    def run_js(self, script):
        if "readyState" in script:
            return "complete"
        if "scrollTo" in script:
            self.scrolls += 1
            self.on_scroll()
            return True
        if "wanted" in script:                                  # the load-more script
            if self.clicks_left <= 0:
                return False
            self.clicks_left -= 1
            self.clicks += 1
            self._render(self.links + 5)
            return True
        return None

    def close(self):
        self.closed = True

    def _render(self, links):
        self.links = links
        anchors = "".join(f'<a href="/careers/list/{n}1234/">Job {n}</a>' for n in range(links))
        self.page_html = f"<html>{'x' * 600}{anchors}</html>"


def count_links(html):
    return html.count('href="/careers/list/')


def listing_pool(driver, **kw):
    ticks = {"now": 0.0}

    def sleep(seconds):
        ticks["now"] += seconds                                 # the fake clock moves only when the pool waits
    kw.setdefault("clock", lambda: ticks["now"])
    return BrowserPool(driver_factory=lambda headless: driver, should_cancel=lambda: False, settle=0, sleep=sleep,
                       load_timeout=5, **kw)


def test_listing_mode_scrolls_and_clicks_load_more_until_the_button_is_gone():
    driver = ListingDriver(clicks=3)
    driver.initial = 3
    pool = listing_pool(driver)
    page = pool.render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    assert page["status"] == 200 and driver.clicks == 3
    assert count_links(page["html"]) == 3 + 15                  # each click added five
    assert driver.scrolls >= 1


def test_listing_mode_waits_for_job_links_to_appear():
    driver = ListingDriver(clicks=0)
    driver.initial = 0
    seen = {"calls": 0}

    def growing(html):
        seen["calls"] += 1
        if seen["calls"] == 4:
            driver._render(6)                                   # the list arrives on the fourth look
        return count_links(driver.page_html)
    pool = listing_pool(driver)
    page = pool.render("https://acme.com/careers/list/", mode="listing", count_links=growing)
    assert seen["calls"] >= 4 and count_links(page["html"]) == 6


def test_listing_mode_gives_up_waiting_after_ten_seconds():
    driver = ListingDriver(clicks=0)
    driver.initial = 0
    pool = listing_pool(driver)
    page = pool.render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    assert page["status"] == 200 and count_links(page["html"]) == 0        # returned instead of waiting forever


def test_listing_mode_stops_clicking_at_max_jobs():
    driver = ListingDriver(clicks=10)
    driver.initial = 3
    pool = listing_pool(driver)
    pool.render("https://acme.com/careers/list/", mode="listing", count_links=count_links, max_jobs=10)
    assert driver.clicks == 2                                   # 3 -> 8 -> 13 >= 10


def test_a_cancel_between_steps_ends_the_exploration():
    driver = ListingDriver(clicks=10)
    driver.initial = 3
    cancel = {"now": False}
    driver.on_scroll = lambda: cancel.update(now=True)          # Stop pressed while scrolling
    pool = listing_pool(driver)
    page = pool.render("https://acme.com/careers/list/", should_cancel=lambda: cancel["now"], mode="listing",
                       count_links=count_links)
    assert driver.clicks == 0 and driver.scrolls == 1 and page["status"] == 200


def test_a_cancel_before_the_wait_skips_it_entirely():
    driver = ListingDriver(clicks=10)
    pool = listing_pool(driver)
    page = pool.render("https://acme.com/careers/list/", should_cancel=lambda: True, mode="listing", count_links=count_links)
    assert page["error"] == "stopped" and driver.clicks == 0


def test_json_responses_from_the_site_are_recorded_and_returned():
    body = json.dumps(uber_style())
    driver = ListingDriver(clicks=0, json_bodies={
        "1": ("application/json", "https://acme.com/api/loadSearchJobsResults", body),
        "2": ("application/json", "https://tracker.example.net/collect", "{}"),                    # someone else's domain
        "3": ("text/html", "https://acme.com/page", "<html/>"),                                    # not JSON
        "4": ("application/json", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/x/jobs", '{"total": 2}'),  # a known platform host
        "5": ("application/json", "https://acme.com/api/broken", "not json"),
    })
    driver.initial = 3
    pool = listing_pool(driver)
    page = pool.render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    urls = [p["url"] for p in page["json_payloads"]]
    assert urls == ["https://acme.com/api/loadSearchJobsResults", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/x/jobs"]
    assert page["json_payloads"][0]["data"]["data"]["results"][0]["title"] == "Software Engineer 0"
    assert "https://tracker.example.net/collect" not in page["json_urls"]


def test_oversized_json_is_skipped():
    huge = json.dumps({"filler": "x" * 2_100_000})
    driver = ListingDriver(json_bodies={"1": ("application/json", "https://acme.com/api/big", huge)})
    driver.initial = 3
    page = listing_pool(driver).render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    assert page["json_payloads"] == []


def test_at_most_thirty_responses_are_kept():
    bodies = {str(n): ("application/json", f"https://acme.com/api/{n}", "{}") for n in range(45)}
    driver = ListingDriver(json_bodies=bodies)
    driver.initial = 3
    page = listing_pool(driver).render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    assert len(page["json_payloads"]) == 30


def test_page_mode_records_nothing_and_adds_no_payload_keys():
    driver = ListingDriver(json_bodies={"1": ("application/json", "https://acme.com/api/x", "{}")})
    driver.initial = 3
    page = listing_pool(driver).render("https://acme.com/careers/list/")
    assert "json_payloads" not in page and driver.scrolls == 0


def test_a_driver_without_response_events_still_renders():
    class Plain(ListingDriver):
        after_response_received = None
    driver = Plain(clicks=1)
    driver.initial = 3
    del Plain.after_response_received
    page = listing_pool(driver).render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    assert page["status"] == 200 and page["json_payloads"] == []


def test_the_capture_is_switched_off_after_a_listing_render():
    driver = ListingDriver(json_bodies={"1": ("application/json", "https://acme.com/api/x", "{}")})
    driver.initial = 3
    pool = listing_pool(driver)
    pool.render("https://acme.com/careers/list/", mode="listing", count_links=count_links)
    assert all(slot.capture is None for slot in pool._slots)
    later = pool.render("https://acme.com/careers/other")                      # a plain render must not be recorded
    assert "json_payloads" not in later


def test_a_bare_type_key_only_counts_when_it_names_an_employment_type():
    data = {"jobs": [{"title": f"Role {n}", "id": n, "type": "Job", "location": "Austin, TX"} for n in range(3)]}
    assert json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)[0]["employment_type"] is None
    data = {"jobs": [{"title": f"Role {n}", "id": n, "type": "Part-Time", "location": "Austin, TX"} for n in range(3)]}
    assert json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)[0]["employment_type"] == "part_time"


def test_content_cards_with_a_title_and_a_link_are_not_jobs():
    """JPMorgan Chase's careers page loads a grid of news stories from a JSON service: title, link, date, no location."""
    stories = {"items": [{"title": f"Story {n}", "date": "July 29, 2026", "description": "Learn about our people",
                          "type": "default", "editorialType": "Stories", "link": f"/newsroom/stories/story-{n}",
                          "linkText": "Learn more", "image": f"/dam/{n}.jpg"} for n in range(60)],
               "meta": {"total-items": 155, "page": 1}}
    assert json_jobs.extract(ScanContext(), [payload(stories)], PAGE, SITE) == []


def test_a_job_specific_id_is_evidence_on_its_own():
    data = {"rows": [{"title": f"Role {n}", "requisitionId": f"REQ{n}"} for n in range(4)]}
    jobs = json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE)
    assert len(jobs) == 4 and jobs[0]["url"].endswith("#job-REQ0")


def test_a_bare_id_and_title_is_not_enough():
    data = {"rows": [{"id": n, "name": f"Thing {n}"} for n in range(5)]}
    assert json_jobs.extract(ScanContext(), [payload(data)], PAGE, SITE) == []


def test_a_loaded_page_that_stays_tiny_is_returned_after_a_short_grace_not_the_full_timeout():
    """Bot-protection pages are small. Waiting 45 s for them to "grow" made a blocked site cost minutes."""
    class Tiny(ListingDriver):
        def get(self, url, timeout=60):
            self.current_url, self.page_html = url, "<html>Access Denied</html>"
    ticks = {"now": 0.0}
    pool = BrowserPool(driver_factory=lambda h: Tiny(), should_cancel=lambda: False, settle=0, load_timeout=45,
                       sleep=lambda s: ticks.__setitem__("now", ticks["now"] + s), clock=lambda: ticks["now"])
    page = pool.render("https://acme.com/careers")
    assert page["status"] == 200 and "Access Denied" in page["html"]
    assert ticks["now"] < 10                                          # seconds of (fake) waiting, not 45
