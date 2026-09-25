"""One visible Chrome window: the UI lives in tab 1, scraping happens in sibling tabs.

Two botasaurus hazards are handled here:
  * driver.get() always navigates the FIRST tab, which is the UI - so it is never used
    after startup; scraping goes through open_link_in_new_tab().
  * A freshly opened tab reports readyState "complete" while still blank, so the first
    scrape of a session returns an empty page unless we wait for the real document.
"""
import threading
import time

from botasaurus_driver import Driver

from .fetch import log

LOAD_TIMEOUT = 45
SETTLE = 1.0          # let client-side job widgets finish after the document is ready


class SharedBrowser:
    def __init__(self, headless=False):
        self.headless = headless
        self.driver = None
        self.ui_tab = None
        self._lock = threading.Lock()   # one driver has a single "current tab" pointer

    def start(self, ui_url):
        self.driver = Driver(headless=self.headless, block_images_and_css=False)
        self.driver.get(ui_url)          # first tab: this is the UI, and stays the UI
        self.ui_tab = self.driver._tab
        return self.driver

    def is_alive(self):
        """Is the window still open?

        Checks the UI tab directly: driver.run_js() would target whichever tab is current,
        which during a scrape is the scraping tab - that races with the scraper and reports
        a dead browser the moment that tab closes.
        """
        if not self.driver or not self.ui_tab:
            return False
        if not self._lock.acquire(blocking=False):
            return True                      # a scrape is in flight, so Chrome is plainly alive
        try:
            self.ui_tab.evaluate("1")
            return True
        except Exception:
            return False
        finally:
            self._lock.release()

    def render(self, url):
        """Load a page in a sibling tab and return a fetch-style page dict."""
        if not self.driver:
            return {"url": url, "final_url": url, "status": 0, "html": "", "error": "browser not started"}
        with self._lock:
            tab = None
            try:
                tab = self.driver.open_link_in_new_tab(url, timeout=LOAD_TIMEOUT)
                html, final_url = self._wait_for_document(url)
                return {"url": url, "final_url": final_url, "status": 200, "html": html, "error": None}
            except Exception as error:
                log(f"browser tab failed for {url}: {type(error).__name__}: {error}")
                return {"url": url, "final_url": url, "status": 0, "html": "", "error": str(error)[:300]}
            finally:
                try:
                    if tab is not None:
                        tab.close()
                except Exception:
                    pass
                try:
                    self.driver._tab = self.ui_tab      # always hand focus back to the UI
                except Exception:
                    pass

    def _wait_for_document(self, url):
        deadline = time.time() + LOAD_TIMEOUT
        html, final_url = "", url
        while time.time() < deadline:
            try:
                final_url = self.driver.current_url or url
                ready = self.driver.run_js("return document.readyState") == "complete"
                html = self.driver.page_html or ""
                if ready and not final_url.startswith("about:") and len(html) > 500:
                    time.sleep(SETTLE)
                    return self.driver.page_html or html, self.driver.current_url or final_url
            except Exception:
                pass
            time.sleep(0.25)
        return html, final_url

    def close(self):
        if not self.driver:
            return
        try:
            self.driver.close()
        except Exception:
            pass
        self.driver = None
