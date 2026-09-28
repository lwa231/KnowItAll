"""HTTP and browser fetching built on botasaurus, plus small page helpers.

Every network call in KnowItAll goes through this module so that caching,
timeouts and fallbacks behave the same everywhere.

Two HTTP stacks are involved on purpose: botasaurus' humane client first (browser-like TLS and headers, which
gets past most bot protection), then plain `requests` when it errors. They differ in TLS fingerprint and
cookie handling, so a page that loads for one may not load for the other; stack_stats() says how often each
served a request, and a request that had to fall back is noted in the log file. Neither client can be
interrupted from outside, so a request that outlives its deadline is abandoned (its thread finishes by
itself, and is counted in stack_stats()['abandoned']) rather than killed.
"""
import hashlib
import logging
import re
import socket
import threading
import traceback
from collections import OrderedDict
from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlparse

import requests as plain_requests
from bs4 import BeautifulSoup
from botasaurus.browser import browser, Driver
from botasaurus.dontcache import DontCache
from botasaurus.request import request, Request

from . import compat

# A redirect to a dead host (seen on servicenow.com) would stall a run; see compat.py.
compat.apply()

BLOCKED_STATUS = {403, 429, 503}      # bot protection: worth retrying in a real browser
SPA_SHELL_RE = re.compile(r'<div id="(root|app|__next)"[^>]*>\s*</div>', re.I)


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


_logger = logging.getLogger("knowitall")     # file handler is attached by paths.enter_data_dir()


def log(message):
    """Console + rotating log file. Per-scan sinks (the UI feed) hang off ScanContext.log."""
    print(f"[knowitall] {message}", flush=True)
    _logger.info(message)


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


def log_exception(message):
    """Console traceback plus the same text in the log file (a packaged app may have no console)."""
    traceback.print_exc()
    _logger.error("%s\n%s", message, traceback.format_exc())


_STATS = {"botasaurus": 0, "requests": 0, "timeouts": 0}
_ABANDONED = []                        # threads left running after their deadline passed
_stats_lock = threading.Lock()


def _count(key):
    with _stats_lock:
        _STATS[key] += 1


def stack_stats():
    """{'botasaurus': n, 'requests': n, 'timeouts': n, 'abandoned': threads still running after a timeout}."""
    with _stats_lock:
        _ABANDONED[:] = [t for t in _ABANDONED if t.is_alive()]
        return {**_STATS, "abandoned": len(_ABANDONED)}


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
        with _stats_lock:
            _STATS["timeouts"] += 1
            _ABANDONED.append(thread)
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
            response = _run_with_deadline(lambda: req.post(url, json=json, headers=headers, timeout=TIMEOUT))
        else:
            response = _run_with_deadline(lambda: req.get(url, headers=headers, timeout=TIMEOUT))
        _count("botasaurus")
        return response
    except Exception as error:
        if _is_dead_host(error):
            raise
        _logger.info("botasaurus client failed for %s (%s: %s); retrying with plain requests",
                     url, type(error).__name__, str(error)[:120])
        # requests' timeout is per socket read, so a slow-trickling server needs the deadline too.
        response = _run_with_deadline(lambda: _plain_request(method, url, headers=headers or {}, json=json))
        _count("requests")
        return response


@request(**_QUIET)
def _fetch_page(req: Request, url):
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


STOPPED_JSON = {"status": 0, "data": None, "error": "stopped"}


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def fetch_page(ctx, url):
    if ctx.should_stop():
        return _page(url, error="stopped")
    return _fetch_page(url, cache=ctx.config.cache)


def fetch_pages(ctx, urls, parallel=8):
    """Fetch several pages at once. Stop is honoured between batches of `parallel`, so at most one
    batch is still in flight when it is pressed; the rest come back as 'stopped'."""
    urls = list(dict.fromkeys(urls))
    pages = []
    for batch in _chunks(urls, parallel):
        if ctx.should_stop():
            pages.extend(_page(url, error="stopped") for url in batch)
            continue
        pages.extend(_fetch_page(batch, cache=ctx.config.cache, parallel=len(batch)))
    return pages


def fetch_json(ctx, url, method="GET", json=None):
    if ctx.should_stop():
        return dict(STOPPED_JSON)
    return _fetch_json({"url": url, "method": method, "json": json}, cache=ctx.config.cache)


def fetch_json_many(ctx, specs, parallel=4):
    specs = list(specs)
    results = []
    for batch in _chunks(specs, parallel):
        if ctx.should_stop():
            results.extend(dict(STOPPED_JSON) for _ in batch)
            continue
        results.extend(_fetch_json(batch, cache=ctx.config.cache, parallel=len(batch)))
    return results


def render_page(ctx, url):
    """Load a page in Chrome: through the scan's renderer (the BrowserPool) when it has one."""
    if ctx.should_stop():
        return _page(url, error="stopped")
    try:
        if ctx.config.renderer:
            page = ctx.config.renderer(url, ctx.should_stop)
        else:
            page = _render_page(url, cache=ctx.config.cache, headless=ctx.config.headless)
    except Exception as error:
        page = None
        ctx.log(f"browser failed: {type(error).__name__}: {error}")
    return page or _page(url, error="browser fallback failed (is Google Chrome installed?)")


def needs_browser(page, thorough=False):
    """Would this page be better read through a real browser? True when the site refused plain requests
    (bot protection) or the page is nearly empty of links. `thorough` also treats a page with hardly any
    visible text, or an empty JavaScript app shell, as needing one (used once a careers page has been found)."""
    if page.get("status") in BLOCKED_STATUS:
        return True
    if not page_ok(page):
        return False
    if len(soup_of(page).find_all("a", href=True)) < 5:
        return True
    return thorough and (visible_text_length(page) < 2000 or bool(SPA_SHELL_RE.search(page["html"])))


def page_ok(page):
    return bool(page) and page.get("status") == 200 and bool(page.get("html"))


class _SoupCache:
    """Parsed pages, keyed by a hash of their HTML and bounded by how many bytes of HTML they came from.

    functools.lru_cache keyed on the HTML string kept every multi-megabyte page alive as a dict key (32 of
    them) next to its parse tree; this keeps only a 16-byte digest, and evicts oldest-first past the budget."""

    def __init__(self, budget_bytes):
        self.budget = budget_bytes
        self._items = OrderedDict()         # digest -> (soup, html size)
        self._total = 0
        self._lock = threading.Lock()

    def get(self, html):
        digest = hashlib.blake2b(html.encode("utf-8", "ignore"), digest_size=16).digest()
        with self._lock:
            hit = self._items.get(digest)
            if hit:
                self._items.move_to_end(digest)
                return hit[0]
        soup = BeautifulSoup(html, "lxml")
        size = len(html)
        if size <= self.budget:
            with self._lock:
                if digest not in self._items:
                    self._items[digest] = (soup, size)
                    self._total += size
                while self._total > self.budget and len(self._items) > 1:
                    _, (_, evicted) = self._items.popitem(last=False)
                    self._total -= evicted
        return soup

    def clear(self):
        with self._lock:
            self._items.clear()
            self._total = 0

    def stats(self):
        with self._lock:
            return {"pages": len(self._items), "html_bytes": self._total}


SOUP_BUDGET_BYTES = 16 * 1024 * 1024
_SOUPS = _SoupCache(SOUP_BUDGET_BYTES)
NON_VISIBLE_TAGS = {"script", "style", "noscript", "template", "svg"}


def soup_of(page):
    # lxml instead of botasaurus' soupify (html.parser): careers pages can be several MB.
    return _SOUPS.get(page.get("html") or "")


def page_title(page):
    tag = soup_of(page).find("title")
    return re.sub(r"\s+", " ", tag.get_text()).strip() if tag else ""


def visible_text_length(page):
    """Characters of human-readable text on the page. Reads the cached parse tree without changing it."""
    pieces = []
    for text in soup_of(page).find_all(string=True):
        if any(parent.name in NON_VISIBLE_TAGS for parent in text.parents):
            continue
        piece = re.sub(r"\s+", " ", text).strip()
        if piece:
            pieces.append(piece)
    return len(" ".join(pieces))
