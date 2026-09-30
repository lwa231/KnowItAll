"""Failures are never cached (DNS blips, block pages), DNS answers expire quickly, timeouts do not double the wait,
and bot-protection pages are recognised."""
import socket

import pytest

from knowitall import fetch


# ---------- 1C: DNS ----------

@pytest.fixture
def dns(monkeypatch):
    """Scriptable DNS: dns.answers is a list of booleans, consumed one per lookup; a fake clock drives the TTLs."""
    fetch.clear_dns_cache()
    state = {"answers": [], "lookups": [], "now": 1000.0}

    def lookup(host):
        state["lookups"].append(host)
        return state["answers"].pop(0) if state["answers"] else True

    monkeypatch.setattr(fetch, "_lookup", lookup)
    monkeypatch.setattr(fetch, "DNS_RETRY_DELAY", 0)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: state["now"])
    yield type("Dns", (), {"state": state})
    fetch.clear_dns_cache()


def test_a_failed_lookup_is_retried_once_before_the_host_is_declared_dead(dns):
    dns.state["answers"] = [False, True]
    assert fetch.host_resolves("blip.example.com") is True
    assert len(dns.state["lookups"]) == 2


def test_a_dead_host_is_remembered_for_a_minute_only(dns):
    dns.state["answers"] = [False, False]
    assert fetch.host_resolves("gone.example.com") is False
    assert fetch.host_resolves("gone.example.com") is False
    assert len(dns.state["lookups"]) == 2                          # the second call was answered from memory
    dns.state["now"] += fetch.DNS_TTL_MISSING + 1
    dns.state["answers"] = [True]
    assert fetch.host_resolves("gone.example.com") is True         # a minute later it is looked up again


def test_a_good_answer_lasts_ten_minutes(dns):
    assert fetch.host_resolves("ok.example.com") is True
    dns.state["now"] += fetch.DNS_TTL_FOUND - 1
    assert fetch.host_resolves("ok.example.com") is True
    assert len(dns.state["lookups"]) == 1
    dns.state["now"] += 2
    assert fetch.host_resolves("ok.example.com") is True
    assert len(dns.state["lookups"]) == 2


def test_a_wifi_blip_does_not_make_a_company_unreachable_for_the_session(dns):
    dns.state["answers"] = [False, False]
    assert fetch.host_resolves("acme.com") is False                # the blip
    dns.state["answers"] = [True]
    assert fetch.clear_dns_cache(negative_only=True) == 1          # what Runner.start does before every run
    assert fetch.host_resolves("acme.com") is True                 # the next scan reaches the site


def test_clearing_only_the_negative_answers_keeps_the_good_ones(dns):
    dns.state["answers"] = [True, False, False]
    fetch.host_resolves("good.example.com")
    fetch.host_resolves("bad.example.com")
    assert fetch.clear_dns_cache(negative_only=True) == 1
    assert fetch.clear_dns_cache() == 1


def test_an_empty_host_never_resolves_and_is_not_looked_up(dns):
    assert fetch.host_resolves("") is False and dns.state["lookups"] == []


def test_the_real_lookup_treats_os_errors_as_not_resolving(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("nope")))
    assert fetch._lookup("x.invalid") is False


# ---------- 1C: failures are not written to the page cache ----------

def cache_files(folder):
    return sorted(p.name for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


class FakeResponse:
    def __init__(self, status=200, text="<html><title>Careers</title></html>", url="https://ok.example.com/"):
        self.status_code, self.text, self.url = status, text, url

    def json(self):
        return {"ok": True}


def test_dns_failures_are_never_cached_but_real_answers_are(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                    # botasaurus keeps its cache/ under the working directory
    monkeypatch.setattr(fetch, "host_resolves", lambda host: host != "gone.example.com")
    monkeypatch.setattr(fetch, "_send", lambda req, method, url, headers=None, json=None: FakeResponse(url=url))

    good = fetch._fetch_page("https://ok.example.com/", cache=True)
    assert good["status"] == 200
    cached = cache_files(tmp_path / "cache")
    assert cached, "the control: a real answer is cached"

    dead = fetch._fetch_page("https://gone.example.com/", cache=True)
    assert dead["error"] == "host does not resolve"
    assert cache_files(tmp_path / "cache") == cached               # the failure left no file behind

    dead_json = fetch._fetch_json({"url": "https://gone.example.com/api"}, cache=True)
    assert dead_json["error"] == "host does not resolve"
    assert cache_files(tmp_path / "cache") == cached


def test_a_recovered_host_is_fetched_not_served_from_a_cached_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    up = {"value": False}
    monkeypatch.setattr(fetch, "host_resolves", lambda host: up["value"])
    monkeypatch.setattr(fetch, "_send", lambda req, method, url, headers=None, json=None: FakeResponse(url=url))
    assert fetch._fetch_page("https://flaky.example.com/", cache=True)["error"] == "host does not resolve"
    up["value"] = True
    page = fetch._fetch_page("https://flaky.example.com/", cache=True)
    assert page["status"] == 200 and page["error"] is None


def test_a_challenge_page_that_answers_200_is_reported_blocked_and_not_cached(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch, "host_resolves", lambda host: True)
    block = "<html><head><title>Access Denied</title></head><body>Reference #18.4b1e1102 errors.edgesuite.net</body></html>"
    monkeypatch.setattr(fetch, "_send", lambda req, method, url, headers=None, json=None: FakeResponse(text=block, url=url))
    page = fetch._fetch_page("https://guarded.example.com/careers", cache=True)
    assert page["status"] == 403 and page["challenge"] is True
    assert fetch.is_blocked(page) and not fetch.page_ok(page)
    assert cache_files(tmp_path / "cache") == []


# ---------- 1B: timeouts ----------

class Req:
    def get(self, url, headers=None, timeout=None):
        raise AssertionError("not reached")


def test_a_botasaurus_timeout_does_not_fall_back_to_plain_requests(monkeypatch):
    monkeypatch.setattr(fetch, "_run_with_deadline", lambda fn, seconds=0: (_ for _ in ()).throw(TimeoutError("no response after 30s")))
    monkeypatch.setattr(fetch, "_plain_request", lambda *a, **k: pytest.fail("retried a slow site with the other client"))
    with pytest.raises(TimeoutError):
        fetch._send(Req(), "GET", "https://slow.example.com/")


def test_library_timeouts_are_recognised_by_name(monkeypatch):
    class ReadTimeout(Exception):
        pass
    monkeypatch.setattr(fetch, "_run_with_deadline", lambda fn, seconds=0: (_ for _ in ()).throw(ReadTimeout("slow")))
    monkeypatch.setattr(fetch, "_plain_request", lambda *a, **k: pytest.fail("fell back after a timeout"))
    with pytest.raises(ReadTimeout):
        fetch._send(Req(), "GET", "https://slow.example.com/")


def test_other_errors_still_fall_back_to_plain_requests(monkeypatch):
    calls = []

    def run(fn, seconds=0):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("tls handshake failed")
        return fn()
    monkeypatch.setattr(fetch, "_run_with_deadline", run)
    monkeypatch.setattr(fetch, "_plain_request", lambda *a, **k: "plain-response")
    assert fetch._send(Req(), "GET", "https://x.example.com/") == "plain-response"


def test_a_fetch_may_not_take_longer_than_thirty_seconds():
    assert fetch.DEADLINE == 30


def test_the_sitemap_lookup_is_capped_at_thirty_seconds():
    from knowitall import discovery
    assert discovery.SITEMAP_SECONDS == 30


# ---------- 1A: recognising bot protection ----------

def html(title="", body="", links=0):
    anchors = "".join(f'<a href="/{i}">x</a>' for i in range(links))
    return f"<html><head><title>{title}</title></head><body>{body}{anchors}</body></html>"


@pytest.mark.parametrize("page_html", [
    html("Just a moment..."),                                                    # Cloudflare
    html("Attention Required! | Cloudflare"),
    html("Access Denied", "Reference #18.4b1e1102.1712345678.1a2b3c"),           # Akamai
    html("Pardon Our Interruption"),                                             # Imperva/Incapsula
    html("", '<script src="/_Incapsula_Resource?SWJIYLWA=1"></script>'),
    html("", '<div id="px-captcha"></div>'),                                     # PerimeterX
    html("", '<iframe src="https://geo.captcha-delivery.com/captcha/"></iframe>'),  # DataDome
    html("Security check", "Please verify you are human"),
    html("", "Please complete the captcha to continue"),
    html("", "See errors.edgesuite.net/18.4b1e1102"),
])
def test_challenge_pages_are_recognised(page_html):
    assert fetch.is_challenge({"html": page_html})


@pytest.mark.parametrize("page_html", [
    html("Careers at Acme", "We are hiring. Protected by reCAPTCHA.", links=20),   # a real page that mentions a captcha
    html("Jobs", "x" * 100_000 + "captcha"),                                      # too big to be a block page
    html("Access to our office"),                                                 # title merely starts similarly
    html("Acme", "Open positions", links=10),
    "",
])
def test_ordinary_pages_are_not_challenges(page_html):
    assert not fetch.is_challenge({"html": page_html})


def test_is_blocked_covers_status_codes_and_challenges():
    assert fetch.is_blocked({"status": 403}) and fetch.is_blocked({"status": 429}) and fetch.is_blocked({"status": 503})
    assert fetch.is_blocked({"status": 403, "challenge": True})
    assert not fetch.is_blocked({"status": 404}) and not fetch.is_blocked({"status": 200, "html": "x"})
    assert not fetch.is_blocked(None)


def test_a_challenge_from_the_browser_is_a_blocked_page(monkeypatch):
    from knowitall.context import RunConfig, ScanContext
    block = html("Just a moment...")
    renderer = lambda url, cancel: {"url": url, "final_url": url, "status": 200, "html": block, "error": None}
    page = fetch.render_page(ScanContext(config=RunConfig(renderer=renderer)), "https://guarded.example.com/")
    assert page["status"] == 403 and page["challenge"] and not fetch.page_ok(page)


# ---------- 1F: render modes ----------

def test_the_listing_mode_reaches_a_renderer_that_understands_it_and_not_one_that_does_not():
    from knowitall.context import RunConfig, ScanContext
    seen = {}

    def modern(url, should_cancel, mode="page", count_links=None, max_jobs=None):
        seen.update(mode=mode, count_links=count_links, max_jobs=max_jobs)
        return {"url": url, "final_url": url, "status": 200, "html": "<html/>", "error": None}

    counter = lambda html: 3
    ctx = ScanContext(config=RunConfig(renderer=modern, max_jobs=77))
    fetch.render_page(ctx, "https://x.com/a", mode="listing", count_links=counter)
    assert seen == {"mode": "listing", "count_links": counter, "max_jobs": 77}

    legacy = lambda url, should_cancel: {"url": url, "final_url": url, "status": 200, "html": "<html/>", "error": None}
    page = fetch.render_page(ScanContext(config=RunConfig(renderer=legacy)), "https://x.com/a", mode="listing")
    assert page["status"] == 200                                    # a two-argument renderer still works
