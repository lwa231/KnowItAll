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
import inspect
import logging
import re
import socket
import threading
import time
import traceback
from collections import OrderedDict
from datetime import timedelta
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


TIMEOUT = 20            # seconds per HTTP request (reading the answer)
CONNECT_TIMEOUT = 8     # seconds to open a connection; a host that resolves but never answers is not worth more
DEADLINE = 30           # hard cap per fetch, including any retries inside botasaurus_requests and the fallback
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


# ---------- DNS ----------
# A cheap lookup so probing non-existent subdomains like careers.<domain> skips the HTTP stack. Answers are
# remembered briefly: a name that resolved for 10 minutes, a name that did not for only 60 seconds, so one
# Wi-Fi blip can never make a company "unreachable" for the rest of the session.
DNS_TTL_FOUND, DNS_TTL_MISSING = 600, 60
DNS_RETRY_DELAY = 1.0            # a failed lookup is tried once more before the host is declared dead
_DNS = {}                        # host -> (resolves, expires at monotonic time)
_dns_lock = threading.Lock()


def _lookup(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except (OSError, UnicodeError):
        return False


def host_resolves(host):
    if not host:
        return False
    with _dns_lock:
        known = _DNS.get(host)
        if known and known[1] > time.monotonic():
            return known[0]
    found = _lookup(host)
    if not found:
        time.sleep(DNS_RETRY_DELAY)
        found = _lookup(host)
    with _dns_lock:
        if len(_DNS) >= 2048:
            _DNS.clear()
        _DNS[host] = (found, time.monotonic() + (DNS_TTL_FOUND if found else DNS_TTL_MISSING))
    return found


def clear_dns_cache(negative_only=False):
    """Forget remembered lookups (only the failed ones with negative_only). Returns how many were dropped."""
    with _dns_lock:
        drop = [host for host, (found, _) in _DNS.items() if not (negative_only and found)]
        for host in drop:
            del _DNS[host]
    return len(drop)


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


class Stopped(Exception):
    """The scan was stopped while a request was in flight; the request was abandoned."""


def _run_with_deadline(fn, seconds=DEADLINE, should_cancel=None):
    """Run fn in a helper thread and wait for it in short slices. Gives up (abandoning the thread, which finishes by
    itself) when `seconds` pass (TimeoutError) or `should_cancel()` turns true (Stopped), so neither a slow site nor
    Stop has to wait for a request that cannot be interrupted. seconds=None waits only for cancellation."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as error:
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    end = None if seconds is None else time.monotonic() + seconds
    while True:
        thread.join(0.2 if (should_cancel or seconds is None) else seconds)
        if not thread.is_alive():
            break
        abandon = None
        if should_cancel and should_cancel():
            abandon = Stopped("stopped")
        elif end is not None and time.monotonic() >= end:
            abandon = TimeoutError(f"no response after {seconds}s")
            with _stats_lock:
                _STATS["timeouts"] += 1
        if abandon:
            with _stats_lock:
                _ABANDONED.append(thread)
            raise abandon
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _page(url, final_url=None, status=0, html="", error=None):
    return {"url": url, "final_url": final_url or url, "status": status, "html": html, "error": error}


def _plain_request(method, url, **kwargs):
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9", **kwargs.pop("headers", {})}
    return plain_requests.request(method, url, headers=headers, timeout=(CONNECT_TIMEOUT, TIMEOUT), **kwargs)


def _is_dead_host(error):
    # e.g. a redirect to a host that doesn't exist (servicenow.com/jobs does this): permanent, don't retry.
    return "no such host" in str(error)


def _is_timeout(error):
    # A slow site is not a broken client: retrying with another client only doubles the wait.
    return isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()


def _send(req, method, url, headers=None, json=None):
    """Send with botasaurus' humane client, falling back to plain `requests` if it errors. The whole thing,
    fallback included, has one DEADLINE, so a host that never answers costs 30 seconds, not 30 + 30."""
    started = time.monotonic()
    try:
        if method == "POST":
            response = _run_with_deadline(lambda: req.post(url, json=json, headers=headers, timeout=TIMEOUT))
        else:
            response = _run_with_deadline(lambda: req.get(url, headers=headers, timeout=TIMEOUT))
        _count("botasaurus")
        return response
    except Exception as error:
        if _is_dead_host(error) or _is_timeout(error):
            raise
        _logger.info("botasaurus client failed for %s (%s: %s); retrying with plain requests",
                     url, type(error).__name__, str(error)[:120])
        # requests' timeout is per socket read, so a slow-trickling server needs the deadline too.
        remaining = max(DEADLINE - (time.monotonic() - started), 2)
        response = _run_with_deadline(lambda: _plain_request(method, url, headers=headers or {}, json=json), seconds=remaining)
        _count("requests")
        return response


def _page_body(req, url):
    if not host_resolves(urlparse(url).hostname or ""):
        return DontCache(_page(url, error="host does not resolve"))
    try:
        response = _send(req, "GET", url)
    except Exception as error:
        page = _page(url, error=f"{type(error).__name__}: {error}"[:300])
        return page if _is_dead_host(error) else DontCache(page)
    page = _page(url, str(response.url), response.status_code, response.text or "")
    if response.status_code == 200 and is_challenge(page):
        return DontCache({**page, "status": 403, "challenge": True})       # a block page dressed as a 200
    if response.status_code in RETRYABLE_STATUS:
        return DontCache(page)
    return page


def _json_body(req, spec):
    url = spec["url"]
    if not host_resolves(urlparse(url).hostname or ""):
        return DontCache({"status": 0, "data": None, "error": "host does not resolve"})
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


# "Reuse downloaded pages for": botasaurus fixes a cache entry's lifetime where the function is decorated, so there is
# one pair of fetchers per lifetime. The 1-hour pair has its own names, hence its own cache folder: an entry written
# with a 12-hour life can never be served to someone who asked for one hour. "Off" bypasses the cache (cache="REFRESH").
@request(**_QUIET)
def _fetch_page(req: Request, url):
    return _page_body(req, url)


@request(**_QUIET)
def _fetch_json(req: Request, spec):
    return _json_body(req, spec)


_QUIET_1H = {**_QUIET, "expires_in": timedelta(hours=1)}


@request(**_QUIET_1H)
def _fetch_page_1h(req: Request, url):
    return _page_body(req, url)


@request(**_QUIET_1H)
def _fetch_json_1h(req: Request, spec):
    return _json_body(req, spec)


def _page_fetcher(ctx):
    return _fetch_page_1h if ctx.config.reuse == "1h" else _fetch_page


def _json_fetcher(ctx):
    return _fetch_json_1h if ctx.config.reuse == "1h" else _fetch_json


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


def _cancellable(ctx, fn, fallback):
    """fn() in a way Stop can interrupt; `fallback` is what a stopped call returns."""
    try:
        return _run_with_deadline(fn, None, ctx.should_stop)
    except Stopped:
        return fallback


def fetch_page(ctx, url):
    if ctx.should_stop():
        return _page(url, error="stopped")
    return _cancellable(ctx, lambda: _page_fetcher(ctx)(url, cache=ctx.config.cache), _page(url, error="stopped"))


def fetch_pages(ctx, urls, parallel=4):
    """Fetch several pages at once, `parallel` at a time. Stop is honoured while a batch is in flight (it is abandoned)
    and between batches; everything not yet answered comes back as 'stopped'."""
    urls = list(dict.fromkeys(urls))
    pages = []
    for batch in _chunks(urls, parallel):
        if ctx.should_stop():
            pages.extend(_page(url, error="stopped") for url in batch)
            continue
        stopped = [_page(url, error="stopped") for url in batch]
        pages.extend(_cancellable(
            ctx, lambda batch=batch: _page_fetcher(ctx)(batch, cache=ctx.config.cache, parallel=len(batch)), stopped))
    return pages


def fetch_json(ctx, url, method="GET", json=None):
    if ctx.should_stop():
        return dict(STOPPED_JSON)
    return _cancellable(ctx, lambda: _json_fetcher(ctx)({"url": url, "method": method, "json": json}, cache=ctx.config.cache),
                        dict(STOPPED_JSON))


def fetch_json_many(ctx, specs, parallel=4):
    specs = list(specs)
    results = []
    for batch in _chunks(specs, parallel):
        if ctx.should_stop():
            results.extend(dict(STOPPED_JSON) for _ in batch)
            continue
        stopped = [dict(STOPPED_JSON) for _ in batch]
        results.extend(_cancellable(
            ctx, lambda batch=batch: _json_fetcher(ctx)(batch, cache=ctx.config.cache, parallel=len(batch)), stopped))
    return results


def _call_renderer(ctx, url, mode, count_links):
    renderer = ctx.config.renderer
    if mode == "page":
        return renderer(url, ctx.should_stop)
    try:
        accepts = inspect.signature(renderer).parameters
    except (TypeError, ValueError):
        accepts = {}
    if "mode" in accepts or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in accepts.values()):
        return renderer(url, ctx.should_stop, mode=mode, count_links=count_links, max_jobs=ctx.config.max_jobs)
    return renderer(url, ctx.should_stop)               # a renderer that only knows how to load a page


def render_page(ctx, url, mode="page", count_links=None):
    """Load a page in Chrome: through the scan's renderer (the BrowserPool) when it has one.

    mode="listing" also scrolls, clicks "load more" and records the JSON the page loads (see BrowserPool.render);
    `count_links(html)` says how many job links a listing shows so far (to know when to stop waiting or scrolling)."""
    if ctx.should_stop():
        return _page(url, error="stopped")
    ctx.phase("Loading the page in Chrome…")
    try:
        if ctx.config.renderer:
            page = _call_renderer(ctx, url, mode, count_links)
        else:
            page = _render_page(url, cache=ctx.config.cache, headless=ctx.config.headless)
    except Exception as error:
        page = None
        ctx.log(f"browser failed: {type(error).__name__}: {error}")
    if page and page.get("status") == 200 and is_challenge(page):
        ctx.log(f"{url} answered with a bot-protection page even in Chrome")
        page = {**page, "status": 403, "challenge": True}
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


# Bot-protection "challenge" pages. Many answer 200 with a tiny page, so the status alone does not give them away.
CHALLENGE_TITLE_RE = re.compile(
    r"^\s*(access denied|just a moment|attention required|pardon our interruption|are you a robot|robot or human"
    r"|verify you are|security check|checking your browser|request blocked|one more step)", re.I)
CHALLENGE_BODY_RE = re.compile(
    r"errors\.edgesuite\.net|_Incapsula_Resource|px-captcha|captcha-delivery|cf-browser-verification"
    r"|/cdn-cgi/challenge-platform|Reference\s*#\s*[0-9a-f]+\.[0-9a-f]+|\bcaptcha\b|enable javascript and cookies", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
CHALLENGE_MAX_HTML = 60_000       # a real careers page is bigger than any block page
CHALLENGE_MAX_LINKS = 8


def is_challenge(page):
    """Is this the bot-protection page (Cloudflare, Akamai, Incapsula, PerimeterX, DataDome) instead of the site?

    A title such as "Access Denied" or "Just a moment..." is enough. Body markers only count on a small page with
    hardly any links, so an ordinary page that merely mentions a captcha is not mistaken for a block."""
    html = (page or {}).get("html") or ""
    if not html:
        return False
    title = TITLE_RE.search(html[:30_000])
    if title and CHALLENGE_TITLE_RE.search(re.sub(r"\s+", " ", title.group(1)).strip()):
        return True
    return (len(html) <= CHALLENGE_MAX_HTML and html.lower().count("<a ") < CHALLENGE_MAX_LINKS
            and bool(CHALLENGE_BODY_RE.search(html)))


def is_blocked(page):
    """The site refused this request: a bot-protection status or a challenge page (even one that answered 200)."""
    page = page or {}
    return page.get("status") in BLOCKED_STATUS or bool(page.get("challenge"))


def page_ok(page):
    return bool(page) and page.get("status") == 200 and bool(page.get("html")) and not page.get("challenge")


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
