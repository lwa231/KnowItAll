"""The scraper's own headless Chrome. The UI never uses it (the UI is a pywebview window).

A small pool of independent botasaurus Drivers, each with its own Chrome process and profile, so
there is no shared "current tab" pointer to guard. Behaviour:

  * lazy: nothing starts until the first page that needs JavaScript;
  * idle: a driver that has been unused for IDLE_SECONDS is closed and restarted on demand;
  * crashes: a driver that dies mid-render is discarded and the render retried once on a fresh one;
  * no Chrome: the first failed start marks the pool unavailable, calls `on_unavailable` once, and
    every later render returns an error immediately, so scraping carries on over plain HTTP;
  * Stop: queued renders are skipped once cancellation is set; a render already running finishes.

Two ways to render. mode="page" loads the page and returns its HTML. mode="listing" is for JavaScript careers
sites that draw their job list from an internal API: it also records the JSON responses the page loads (the
postings are often only there), waits for job links to appear, scrolls to the bottom while more keep coming, and
clicks "load more" style buttons. Stop is checked between every one of those steps.
"""
import json
import threading
import time
from urllib.parse import urlparse

from .fetch import log

LOAD_TIMEOUT = 45     # seconds to wait for a page
SETTLE = 1.0          # let client-side job widgets finish after the document is ready
SMALL_PAGE_GRACE = 3.0    # a loaded page that stays tiny (a block page, an empty shell) is returned after this long
IDLE_SECONDS = 120    # close a driver this long after its last render
REAP_INTERVAL = 15    # how often the idle check runs
MAX_POOL = 3

# listing mode
LISTING_WAIT = 10.0       # seconds to wait for job links to show up
LISTING_POLL = 0.5
MAX_SCROLLS = 5
SCROLL_PAUSE = 1.0
MAX_LOAD_MORE_CLICKS = 10
CLICK_PAUSE = 1.5
ENOUGH_LINKS = 3
MAX_JSON_RESPONSES = 30
MAX_JSON_BYTES = 2_000_000
# Hosts of hiring platforms whose JSON is worth reading even though they are not the company's own domain.
KNOWN_API_HOSTS = ("myworkdayjobs.com", "oraclecloud.com", "greenhouse.io", "lever.co", "ashbyhq.com",
                   "smartrecruiters.com", "phenompeople.com", "eightfold.ai", "icims.com", "successfactors.com",
                   "successfactors.eu", "taleo.net", "jobvite.com", "workable.com", "bamboohr.com", "recruitee.com",
                   "teamtailor.com", "personio.de", "personio.com")

SCROLL_JS = "window.scrollTo(0, document.body.scrollHeight); return true;"
# Click a "load more" style control. Only buttons and same-page links: a link that leaves the page is not clicked.
LOAD_MORE_JS = """return (() => {
    const wanted = /(load|show|see|view)\\s+more|more\\s+(jobs|results|roles|positions)/i;
    const nodes = document.querySelectorAll('button, [role="button"], a[href^="#"], a[href=""], a:not([href])');
    for (const el of nodes) {
        const text = (el.innerText || el.textContent || '').trim();
        if (!text || text.length > 40 || !wanted.test(text)) continue;
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
        const box = el.getBoundingClientRect();
        if (!box.width || !box.height) continue;
        el.scrollIntoView({block: 'center'});
        el.click();
        return true;
    }
    return false;
})();"""


def _default_driver(headless):
    from botasaurus_driver import Driver          # imported lazily: only the scraper needs it
    return Driver(headless=headless, block_images_and_css=False)


def _clamp_size(size):
    return max(1, min(MAX_POOL, int(size)))


def _page(url, final_url=None, status=0, html="", error=None):
    return {"url": url, "final_url": final_url or url, "status": status, "html": html, "error": error}


class _Slot:
    def __init__(self):
        self.driver = None
        self.last_used = 0.0
        self.busy = False
        self.capture = None        # the _JsonCapture of the listing render in progress, if any


class _JsonCapture:
    """The JSON responses one listing render saw: which ones to keep (by host and type), then their bodies."""

    def __init__(self, page_url):
        from .discovery import registrable_domain
        self._registrable = registrable_domain
        self.domains = {registrable_domain(urlparse(page_url).hostname or "")}
        self.seen = []                                  # [(request_id, url)]
        self._lock = threading.Lock()

    def accepts(self, url):
        host = (urlparse(url).hostname or "").lower()
        return self._registrable(host) in self.domains or any(host == h or host.endswith("." + h) for h in KNOWN_API_HOSTS)

    def add(self, request_id, url):
        with self._lock:
            if len(self.seen) < MAX_JSON_RESPONSES:
                self.seen.append((request_id, url))

    def bodies(self, driver):
        """[{"url", "data"}] for every kept response whose body is JSON of a sensible size."""
        with self._lock:
            seen = list(self.seen)
        payloads = []
        for request_id, url in seen:
            try:
                text = driver.collect_response(request_id).get_decoded_content()
                if isinstance(text, bytes):
                    text = text.decode("utf-8", "replace")
                if not text or len(text) > MAX_JSON_BYTES:
                    continue
                payloads.append({"url": url, "data": json.loads(text)})
            except Exception:
                continue                                # a response the browser no longer holds, or not JSON after all
        return payloads


class BrowserPool:
    def __init__(self, size=1, idle_seconds=IDLE_SECONDS, headless=True, driver_factory=None,
                 should_cancel=None, on_unavailable=None, clock=time.monotonic,
                 load_timeout=LOAD_TIMEOUT, settle=SETTLE, sleep=time.sleep):
        self.size = _clamp_size(size)                # how many browsers may render at once (1..MAX_POOL)
        self.idle_seconds = idle_seconds
        self.headless = headless
        self.unavailable = None                    # reason string once Chrome could not be started
        self._factory = driver_factory or _default_driver
        self._should_cancel = should_cancel or (lambda: False)
        self._on_unavailable = on_unavailable
        self._clock = clock
        self._sleep = sleep
        self._load_timeout = load_timeout
        self._settle = settle
        self._slots = [_Slot() for _ in range(MAX_POOL)]      # only the first `size` are ever used
        self._cond = threading.Condition()         # guards slots/size and wakes renders waiting for a browser
        self._stop = threading.Event()
        self._reaper = None

    # ---------- public ----------
    def resize(self, size):
        """Change how many browsers may render at once. Growing takes effect immediately; shrinking closes
        the surplus browsers as soon as they are idle (a render already running is left to finish)."""
        size = _clamp_size(size)
        with self._cond:
            self.size = size
            surplus = self._take_idle_beyond_size()
            self._cond.notify_all()
        for driver in surplus:
            self._close_driver(driver)
        return size

    def render(self, url, should_cancel=None, mode="page", count_links=None, max_jobs=None):
        """Load `url` in Chrome and return a fetch-style page dict. Never raises.

        `should_cancel` (the calling scan's Stop) is also honoured while waiting for a free browser.
        mode="listing" also explores the page (see the module docstring) and adds "json_payloads" - the JSON the page
        loaded, [{"url", "data"}] - and "json_urls" to the result. `count_links(html)` says how many job links the
        page shows so far; `max_jobs` stops "load more" clicking once that many are showing."""
        if self._stop.is_set():
            return _page(url, error="browser closed")
        if self.unavailable:
            return _page(url, error=self.unavailable)
        slot = self._acquire(should_cancel)
        if slot is None:
            return _page(url, error="stopped")
        try:
            return self._render_on(slot, url, should_cancel, mode, count_links, max_jobs)
        finally:
            with self._cond:
                slot.last_used = self._clock()
                slot.busy = False
                surplus = self._take_idle_beyond_size()
                self._cond.notify_all()
            for driver in surplus:
                self._close_driver(driver)

    def reap_idle(self):
        """Close drivers idle for longer than idle_seconds. Returns how many were closed."""
        stale = []
        with self._cond:
            now = self._clock()
            for slot in self._slots:
                if slot.driver is not None and not slot.busy and now - slot.last_used >= self.idle_seconds:
                    stale.append(slot.driver)
                    slot.driver = None
        for driver in stale:
            self._close_driver(driver)
        if stale:
            log(f"closed {len(stale)} idle browser(s)")
        return len(stale)

    def active_drivers(self):
        with self._cond:
            return sum(1 for slot in self._slots if slot.driver is not None)

    def close(self):
        self._stop.set()
        with self._cond:
            drivers = [slot.driver for slot in self._slots if slot.driver is not None]
            for slot in self._slots:
                slot.driver = None
            self._cond.notify_all()
        for driver in drivers:
            self._close_driver(driver)

    # ---------- internals ----------
    def _take_idle_beyond_size(self):
        """Detach the drivers of idle slots that are no longer allowed (call with the lock held)."""
        taken = []
        for slot in self._slots[self.size:]:
            if slot.driver is not None and not slot.busy:
                taken.append(slot.driver)
                slot.driver = None
        return taken

    def _acquire(self, should_cancel=None):
        with self._cond:
            while not self._stop.is_set():
                if self._should_cancel() or (should_cancel and should_cancel()):
                    return None
                for slot in self._slots[:self.size]:
                    if not slot.busy:
                        slot.busy = True
                        return slot
                self._cond.wait(0.25)
        return None

    def _attach_listener(self, slot, driver):
        """Watch the driver's responses once, when it is created; it records only while a listing render is on."""
        register = getattr(driver, "after_response_received", None)
        if register is None:
            return

        def on_response(request_id, response, event=None):
            capture = slot.capture
            if capture is None:
                return
            try:
                kind = str(getattr(response, "mime_type", "") or "").lower()
                target = str(getattr(response, "url", "") or "")
                if "json" in kind and capture.accepts(target):
                    capture.add(request_id, target)
            except Exception:
                pass

        try:
            register(on_response)
        except Exception as error:
            log(f"could not watch the browser's network responses: {type(error).__name__}")

    def _render_on(self, slot, url, should_cancel=None, mode="page", count_links=None, max_jobs=None):
        for attempt in (1, 2):
            with self._cond:
                driver, reused = slot.driver, slot.driver is not None
            if driver is None:
                try:
                    driver = self._factory(self.headless)
                except Exception as error:
                    return self._mark_unavailable(url, error)
                self._attach_listener(slot, driver)
                with self._cond:
                    slot.driver = driver
                self._ensure_reaper()
            try:
                slot.capture = _JsonCapture(url) if mode == "listing" else None
                html, final_url = self._load(driver, url, should_cancel)
                page = _page(url, final_url, 200, html)
                if mode == "listing":
                    page["html"] = self._explore(driver, html, should_cancel, count_links, max_jobs)
                    page["final_url"] = self._current_url(driver, final_url)
                    payloads = slot.capture.bodies(driver)
                    page["json_payloads"] = payloads
                    page["json_urls"] = [u for _, u in slot.capture.seen]
                return page
            except Exception as error:
                with self._cond:
                    if slot.driver is driver:
                        slot.driver = None
                self._close_driver(driver)
                if attempt == 1 and reused:
                    log(f"browser was no longer usable ({type(error).__name__}); restarting it")
                    continue
                log(f"browser failed for {url}: {type(error).__name__}: {error}")
                return _page(url, error=str(error)[:300] or type(error).__name__)
            finally:
                slot.capture = None
        return _page(url, error="browser failed")

    @staticmethod
    def _current_url(driver, fallback):
        try:
            return driver.current_url or fallback
        except Exception:
            return fallback

    def _cancelled(self, should_cancel):
        return self._stop.is_set() or self._should_cancel() or bool(should_cancel and should_cancel())

    def _explore(self, driver, html, should_cancel, count_links, max_jobs):
        """Listing mode: wait for job links, scroll while more appear, click "load more"; returns the final HTML.
        Every step is skipped once the scan is stopped, and whatever the page shows by then is returned."""
        def links(current):
            return count_links(current) if count_links else 0

        def read():
            return driver.page_html or ""

        found = links(html)
        deadline = self._clock() + LISTING_WAIT
        while found < ENOUGH_LINKS and count_links and self._clock() < deadline and not self._cancelled(should_cancel):
            self._sleep(LISTING_POLL)
            html = read()
            found = links(html)

        for _ in range(MAX_SCROLLS):
            if self._cancelled(should_cancel):
                return html
            driver.run_js(SCROLL_JS)
            self._sleep(SCROLL_PAUSE)
            html = read()
            grown = links(html)
            if grown <= found:
                break
            found = grown

        for _ in range(MAX_LOAD_MORE_CLICKS):
            if self._cancelled(should_cancel) or (max_jobs and found >= max_jobs):
                break
            if not driver.run_js(LOAD_MORE_JS):
                break
            self._sleep(CLICK_PAUSE)
            html = read()
            found = max(found, links(html))
        return html

    def _load(self, driver, url, should_cancel=None):
        driver.get(url, timeout=self._load_timeout)
        deadline = self._clock() + self._load_timeout
        html, final_url = "", url
        ready_since = None
        while self._clock() < deadline and not self._cancelled(should_cancel):
            try:
                final_url = driver.current_url or url
                ready = driver.run_js("return document.readyState") == "complete"
                html = driver.page_html or ""
                if ready and not final_url.startswith("about:"):
                    ready_since = self._clock() if ready_since is None else ready_since
                    # Normally wait for a real-sized page, but a page that has finished loading and stays tiny is
                    # all there is (bot-protection pages are): waiting the full timeout for it only wastes a minute.
                    if len(html) > 500 or self._clock() - ready_since >= SMALL_PAGE_GRACE:
                        self._sleep(self._settle)
                        return driver.page_html or html, driver.current_url or final_url
            except Exception:
                pass
            self._sleep(0.25)
        return html, final_url

    def _mark_unavailable(self, url, error):
        self.unavailable = f"Chrome could not be started ({type(error).__name__}: {str(error)[:200]})"
        log(self.unavailable)
        if self._on_unavailable:
            try:
                self._on_unavailable(self.unavailable)
            except Exception:
                pass
        return _page(url, error=self.unavailable)

    def _ensure_reaper(self):
        with self._cond:
            if self._reaper is not None or self._stop.is_set():
                return
            self._reaper = threading.Thread(target=self._reap_loop, name="browser-reaper", daemon=True)
            self._reaper.start()

    def _reap_loop(self):
        while not self._stop.wait(REAP_INTERVAL):
            try:
                self.reap_idle()
            except Exception as error:
                log(f"idle browser check failed: {error}")

    @staticmethod
    def _close_driver(driver):
        try:
            driver.close()
        except Exception:
            pass
