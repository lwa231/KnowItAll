import threading
import time

import pytest

from knowitall.browser import BrowserPool

HTML = "<html>" + "x" * 600 + "</html>"


class FakeDriver:
    created = []

    def __init__(self, fail_first_loads=0, delay=0.0):
        self.fail_loads = fail_first_loads
        self.delay = delay
        self.closed = False
        self.loads = []
        self.current_url = "about:blank"
        self.page_html = ""
        FakeDriver.created.append(self)

    def get(self, url, timeout=60):
        if self.fail_loads:
            self.fail_loads -= 1
            raise RuntimeError("chrome crashed")
        time.sleep(self.delay)
        self.loads.append(url)
        self.current_url, self.page_html = url, HTML

    def run_js(self, script):
        return "complete"

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_created():
    FakeDriver.created = []


def pool(**kw):
    kw.setdefault("driver_factory", lambda headless: FakeDriver())
    kw.setdefault("should_cancel", lambda: False)
    kw.setdefault("settle", 0)
    return BrowserPool(**kw)


def test_lazy_start_and_successful_render():
    p = pool()
    assert FakeDriver.created == []                      # nothing starts until needed
    page = p.render("https://x.com/jobs")
    assert page["status"] == 200 and page["error"] is None and len(page["html"]) > 500
    assert page["final_url"] == "https://x.com/jobs"
    assert len(FakeDriver.created) == 1
    p.render("https://x.com/other")
    assert len(FakeDriver.created) == 1                  # the driver is reused
    p.close()
    assert FakeDriver.created[0].closed


def test_idle_driver_is_closed_and_restarted_on_demand():
    now = [1000.0]
    p = pool(idle_seconds=120, clock=lambda: now[0])
    p.render("https://x.com/1")
    now[0] += 60
    assert p.reap_idle() == 0                            # not idle long enough
    now[0] += 61
    assert p.reap_idle() == 1
    assert FakeDriver.created[0].closed and p.active_drivers() == 0
    p.render("https://x.com/2")                          # transparently starts a new one
    assert len(FakeDriver.created) == 2 and p.active_drivers() == 1
    p.close()


def test_busy_driver_is_never_reaped():
    now = [0.0]
    started, release = threading.Event(), threading.Event()

    class Slow(FakeDriver):
        def get(self, url, timeout=60):
            started.set()
            release.wait(5)
            super().get(url, timeout)

    p = pool(idle_seconds=1, clock=lambda: now[0], driver_factory=lambda h: Slow())
    thread = threading.Thread(target=p.render, args=("https://x.com/1",))
    thread.start()
    assert started.wait(2)
    now[0] += 500
    assert p.reap_idle() == 0
    release.set()
    thread.join(5)
    p.close()


def test_crashed_driver_is_replaced_and_render_retried_once():
    drivers = iter([FakeDriver(), FakeDriver()])
    p = pool(driver_factory=lambda h: next(drivers))
    p.render("https://x.com/1")
    FakeDriver.created[0].fail_loads = 1                 # the reused driver dies on the next load
    page = p.render("https://x.com/2")
    assert page["status"] == 200
    assert FakeDriver.created[0].closed and len(FakeDriver.created) == 2
    p.close()


def test_failure_on_a_fresh_driver_is_not_retried_forever():
    p = pool(driver_factory=lambda h: FakeDriver(fail_first_loads=5))
    page = p.render("https://x.com/1")
    assert page["status"] == 0 and page["error"]
    assert len(FakeDriver.created) == 1
    p.close()


def test_chrome_missing_degrades_once_and_notifies_once():
    calls, reasons = [], []

    def factory(headless):
        calls.append(1)
        raise FileNotFoundError("chrome not found")

    p = pool(driver_factory=factory, on_unavailable=reasons.append)
    first = p.render("https://x.com/1")
    second = p.render("https://x.com/2")
    assert first["status"] == 0 and "Chrome could not be started" in first["error"]
    assert second["error"] == first["error"]
    assert len(calls) == 1 and len(reasons) == 1        # no restart storm, one notification
    assert p.unavailable


def test_cancel_skips_queued_renders():
    cancelled = [False]
    p = pool(should_cancel=lambda: cancelled[0])
    assert p.render("https://x.com/1")["status"] == 200
    cancelled[0] = True
    page = p.render("https://x.com/2")
    assert page["error"] == "stopped"
    assert len(FakeDriver.created[0].loads) == 1
    p.close()


def test_cancel_does_not_abort_a_render_in_flight():
    cancelled = [False]
    p = pool(should_cancel=lambda: cancelled[0], driver_factory=lambda h: FakeDriver(delay=0.4))
    result = {}
    thread = threading.Thread(target=lambda: result.update(p.render("https://x.com/1")))
    thread.start()
    time.sleep(0.1)
    cancelled[0] = True                                  # Stop pressed mid-render
    thread.join(5)
    assert result["status"] == 200                       # it finished, as agreed
    p.close()


def test_pool_size_bounds_parallel_renders():
    active, peak = [0], [0]
    lock = threading.Lock()

    class Counting(FakeDriver):
        def get(self, url, timeout=60):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.15)
            with lock:
                active[0] -= 1
            super().get(url, timeout)

    p = pool(size=2, driver_factory=lambda h: Counting())
    threads = [threading.Thread(target=p.render, args=(f"https://x.com/{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert peak[0] == 2
    assert len(FakeDriver.created) == 2                  # one driver per slot, never more
    p.close()


def test_size_is_clamped_to_one_through_three():
    assert BrowserPool(size=0).size == 1
    assert BrowserPool(size=9).size == 3


def test_render_after_close_returns_an_error_page():
    p = pool()
    p.close()
    assert p.render("https://x.com/1")["error"] == "browser closed"
