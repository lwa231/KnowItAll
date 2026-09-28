"""The scraper's own headless Chrome. The UI never uses it (the UI is a pywebview window).

A small pool of independent botasaurus Drivers, each with its own Chrome process and profile, so
there is no shared "current tab" pointer to guard. Behaviour:

  * lazy: nothing starts until the first page that needs JavaScript;
  * idle: a driver that has been unused for IDLE_SECONDS is closed and restarted on demand;
  * crashes: a driver that dies mid-render is discarded and the render retried once on a fresh one;
  * no Chrome: the first failed start marks the pool unavailable, calls `on_unavailable` once, and
    every later render returns an error immediately, so scraping carries on over plain HTTP;
  * Stop: queued renders are skipped once cancellation is set; a render already running finishes.
"""
import threading
import time

from .fetch import log

LOAD_TIMEOUT = 45     # seconds to wait for a page
SETTLE = 1.0          # let client-side job widgets finish after the document is ready
IDLE_SECONDS = 120    # close a driver this long after its last render
REAP_INTERVAL = 15    # how often the idle check runs
MAX_POOL = 3


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


class BrowserPool:
    def __init__(self, size=1, idle_seconds=IDLE_SECONDS, headless=True, driver_factory=None,
                 should_cancel=None, on_unavailable=None, clock=time.monotonic,
                 load_timeout=LOAD_TIMEOUT, settle=SETTLE):
        self.size = _clamp_size(size)                # how many browsers may render at once (1..MAX_POOL)
        self.idle_seconds = idle_seconds
        self.headless = headless
        self.unavailable = None                    # reason string once Chrome could not be started
        self._factory = driver_factory or _default_driver
        self._should_cancel = should_cancel or (lambda: False)
        self._on_unavailable = on_unavailable
        self._clock = clock
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

    def render(self, url, should_cancel=None):
        """Load `url` in Chrome and return a fetch-style page dict. Never raises.

        `should_cancel` (the calling scan's Stop) is also honoured while waiting for a free browser."""
        if self._stop.is_set():
            return _page(url, error="browser closed")
        if self.unavailable:
            return _page(url, error=self.unavailable)
        slot = self._acquire(should_cancel)
        if slot is None:
            return _page(url, error="stopped")
        try:
            return self._render_on(slot, url)
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

    def _render_on(self, slot, url):
        for attempt in (1, 2):
            with self._cond:
                driver, reused = slot.driver, slot.driver is not None
            if driver is None:
                try:
                    driver = self._factory(self.headless)
                except Exception as error:
                    return self._mark_unavailable(url, error)
                with self._cond:
                    slot.driver = driver
                self._ensure_reaper()
            try:
                html, final_url = self._load(driver, url)
                return _page(url, final_url, 200, html)
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
        return _page(url, error="browser failed")

    def _load(self, driver, url):
        driver.get(url, timeout=self._load_timeout)
        deadline = self._clock() + self._load_timeout
        html, final_url = "", url
        while self._clock() < deadline:
            try:
                final_url = driver.current_url or url
                ready = driver.run_js("return document.readyState") == "complete"
                html = driver.page_html or ""
                if ready and not final_url.startswith("about:") and len(html) > 500:
                    time.sleep(self._settle)
                    return driver.page_html or html, driver.current_url or final_url
            except Exception:
                pass
            time.sleep(0.25)
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
