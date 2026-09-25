"""HTTP and browser fetching built on botasaurus, plus small page helpers.

Every network call in KnowItAll goes through this module so that caching,
timeouts and fallbacks behave the same everywhere.
"""
import re
import socket
import threading
from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlparse

import requests as plain_requests
from botasaurus_requests import reqs as _botasaurus_reqs
from bs4 import BeautifulSoup
from botasaurus.browser import browser, Driver
from botasaurus.dontcache import DontCache
from botasaurus.request import request, Request

# botasaurus_requests retries "no such host" errors every 20s forever, assuming the internet is down.
# A redirect to a dead host (seen on servicenow.com) would stall a run, so make it a single attempt;
# our own fallback + deadline below handle failures.
_botasaurus_reqs.retry_on_network_error = lambda func: func()


class Settings:
    cache = True          # True, or "REFRESH" to ignore cached pages and re-download them
    headless = True       # run Chrome without a window for the browser fallback
    use_browser = True    # allow the Chrome fallback at all
    max_jobs = 2000       # cap for very large boards (e.g. Workday tenants)
    max_enrich = 50       # job pages to open when reading JSON-LD in the generic fallback

    # Set by the UI: a shared Chrome renderer, a log sink, and a cancellation flag.
    renderer = None       # callable(url) -> page dict, replacing the standalone Chrome
    log_sink = None       # callable(message) called for every log line
    cancel_event = None   # threading.Event; when set, fetching stops as soon as possible


settings = Settings()


def should_stop():
    return bool(settings.cancel_event and settings.cancel_event.is_set())

TIMEOUT = 25            # seconds per HTTP request
DEADLINE = 60           # hard cap per fetch, including any retries inside botasaurus_requests
CACHE_TTL = timedelta(hours=12)
RETRYABLE_STATUS = {403, 408, 429, 500, 502, 503, 504}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# close_on_crash=True matters: outside production botasaurus otherwise pauses on errors
# and waits for Enter, which would freeze a CLI run.
_QUIET = dict(
    output=None,
    close_on_crash=True,
    raise_exception=False,
    create_error_logs=False,
    expires_in=CACHE_TTL,
)


def log(message):
    print(f"[knowitall] {message}", flush=True)
    if settings.log_sink:
        try:
            settings.log_sink(str(message))
        except Exception:
            pass  # a failing UI log must never break a scrape


@lru_cache(maxsize=1024)
def host_resolves(host):
    # Cheap DNS check so probing non-existent subdomains like careers.<domain> skips the HTTP stack.
    if not host:
        return False
    try:
        socket.getaddrinfo(host, 443)
        return True
    except (OSError, UnicodeError):
        return False


def _run_with_deadline(fn, seconds=DEADLINE):
    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as error:
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"no response after {seconds}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _page(url, final_url=None, status=0, html="", error=None):
    return {"url": url, "final_url": final_url or url, "status": status, "html": html, "error": error}


def _plain_request(method, url, **kwargs):
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9", **kwargs.pop("headers", {})}
    return plain_requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)


def _is_dead_host(error):
    # e.g. a redirect to a host that doesn't exist (servicenow.com/jobs does this): permanent, don't retry.
    return "no such host" in str(error)


def _send(req, method, url, headers=None, json=None):
    """Send with botasaurus' humane client, falling back to plain `requests` if it errors."""
    try:
        if method == "POST":
            return _run_with_deadline(lambda: req.post(url, json=json, headers=headers, timeout=TIMEOUT))
        return _run_with_deadline(lambda: req.get(url, headers=headers, timeout=TIMEOUT))
    except Exception as error:
        if _is_dead_host(error):
            raise
        # requests' timeout is per socket read, so a slow-trickling server needs the deadline too.
        return _run_with_deadline(lambda: _plain_request(method, url, headers=headers or {}, json=json))


@request(**_QUIET)
def _fetch_page(req: Request, url):
    if should_stop():
        return DontCache(_page(url, error="stopped"))
    if not host_resolves(urlparse(url).hostname or ""):
        return _page(url, error="host does not resolve")
    try:
        response = _send(req, "GET", url)
    except Exception as error:
        page = _page(url, error=f"{type(error).__name__}: {error}"[:300])
        return page if _is_dead_host(error) else DontCache(page)
    page = _page(url, str(response.url), response.status_code, response.text or "")
    if response.status_code in RETRYABLE_STATUS:
        return DontCache(page)
    return page


@request(**_QUIET)
def _fetch_json(req: Request, spec):
    url = spec["url"]
    if should_stop():
        return DontCache({"status": 0, "data": None, "error": "stopped"})
    if not host_resolves(urlparse(url).hostname or ""):
        return {"status": 0, "data": None, "error": "host does not resolve"}
    headers = {"Accept": "application/json"}
    if spec.get("method") == "POST":
        headers["Content-Type"] = "application/json"
    try:
        response = _send(req, spec.get("method", "GET"), url, headers=headers, json=spec.get("json"))
    except Exception as error:
        return DontCache({"status": 0, "data": None, "error": f"{type(error).__name__}: {error}"[:300]})
    try:
        data = response.json()
    except Exception:
        data = None
    result = {"status": response.status_code, "data": data}
    if response.status_code in RETRYABLE_STATUS:
        return DontCache(result)
    return result


@browser(headless=True, block_images_and_css=True, **_QUIET)
def _render_page(driver: Driver, url):
    try:
        driver.get(url)
        driver.sleep(3)  # let client-side job widgets finish loading
        return _page(url, driver.current_url, 200, driver.page_html)
    except Exception as error:
        return DontCache(_page(url, error=f"{type(error).__name__}: {error}"[:300]))


def fetch_page(url):
    return _fetch_page(url, cache=settings.cache)


def fetch_pages(urls, parallel=8):
    urls = list(dict.fromkeys(urls))
    if not urls:
        return []
    return _fetch_page(urls, cache=settings.cache, parallel=min(parallel, len(urls)))


def fetch_json(url, method="GET", json=None):
    return _fetch_json({"url": url, "method": method, "json": json}, cache=settings.cache)


def fetch_json_many(specs, parallel=4):
    if not specs:
        return []
    return _fetch_json(list(specs), cache=settings.cache, parallel=min(parallel, len(specs)))


def render_page(url):
    """Load a page in Chrome. Uses the UI's shared browser when one is registered."""
    if should_stop():
        return _page(url, error="stopped")
    try:
        if settings.renderer:
            page = settings.renderer(url)
        else:
            page = _render_page(url, cache=settings.cache, headless=settings.headless)
    except Exception as error:
        page = None
        log(f"browser failed: {type(error).__name__}: {error}")
    return page or _page(url, error="browser fallback failed (is Google Chrome installed?)")


def page_ok(page):
    return bool(page) and page.get("status") == 200 and bool(page.get("html"))


@lru_cache(maxsize=32)
def _soup(html):
    return BeautifulSoup(html, "lxml")


def soup_of(page):
    # lxml instead of botasaurus' soupify (html.parser): careers pages can be several MB.
    return _soup(page.get("html") or "")


def page_title(page):
    tag = soup_of(page).find("title")
    return re.sub(r"\s+", " ", tag.get_text()).strip() if tag else ""


def visible_text_length(page):
    soup = BeautifulSoup(page.get("html") or "", "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return len(re.sub(r"\s+", " ", soup.get_text(" ")).strip())
