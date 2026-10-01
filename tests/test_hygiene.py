import threading
import time

import pytest

from knowitall import compat, discovery, fetch, scraper
from knowitall.runner import Runner


# ---------- L1: the botasaurus coupling lives in compat.py ----------

def test_compat_patch_is_applied_and_idempotent():
    from botasaurus_requests import reqs
    compat.apply()
    compat.apply()
    assert reqs.retry_on_network_error(lambda: "once") == "once"


def test_version_check_warns_only_on_a_mismatch(monkeypatch):
    lines = []
    monkeypatch.setattr(compat, "installed_version", lambda: compat.PINNED_VERSION)
    assert compat.check_version(lines.append) is True and lines == []
    monkeypatch.setattr(compat, "installed_version", lambda: "5.0.0")
    assert compat.check_version(lines.append) is False
    assert "5.0.0" in lines[0] and compat.PINNED_VERSION in lines[0] and "compat.py" in lines[0]
    monkeypatch.setattr(compat, "installed_version", lambda: None)
    assert compat.check_version(lines.append) is False


def test_fetch_no_longer_reaches_into_botasaurus_itself():
    source = open(fetch.__file__, encoding="utf-8").read()
    assert "retry_on_network_error" not in source and "_botasaurus_reqs" not in source


# ---------- L2: two stacks, abandoned threads ----------

class FakeReq:
    def __init__(self, fail=False):
        self.fail = fail

    def get(self, url, headers=None, timeout=None):
        if self.fail:
            raise ConnectionError("tls handshake failed")
        return "botasaurus-response"

    post = get


def test_stack_stats_count_which_client_served_and_fallbacks_are_logged(monkeypatch):
    before = fetch.stack_stats()
    assert fetch._send(FakeReq(), "GET", "https://x.com/a") == "botasaurus-response"
    monkeypatch.setattr(fetch, "_plain_request", lambda method, url, **kw: "requests-response")
    logged = []
    monkeypatch.setattr(fetch._logger, "info", lambda *a: logged.append(a))
    assert fetch._send(FakeReq(fail=True), "GET", "https://x.com/b") == "requests-response"
    after = fetch.stack_stats()
    assert after["botasaurus"] == before["botasaurus"] + 1 and after["requests"] == before["requests"] + 1
    assert logged and "https://x.com/b" in logged[0][1]                       # the fallback names the URL


def test_dead_hosts_are_not_retried_on_the_other_stack(monkeypatch):
    class Dead(FakeReq):
        def get(self, url, headers=None, timeout=None):
            raise ConnectionError("no such host")
    monkeypatch.setattr(fetch, "_plain_request", lambda *a, **k: pytest.fail("fell back for a dead host"))
    with pytest.raises(ConnectionError):
        fetch._send(Dead(), "GET", "https://gone.invalid/")


def test_a_request_past_its_deadline_is_abandoned_counted_and_gone_when_it_ends():
    release = threading.Event()
    before = fetch.stack_stats()
    with pytest.raises(TimeoutError):
        fetch._run_with_deadline(lambda: release.wait(5), seconds=0.05)
    during = fetch.stack_stats()
    assert during["timeouts"] == before["timeouts"] + 1 and during["abandoned"] >= before["abandoned"] + 1
    release.set()
    deadline = time.time() + 2
    while fetch.stack_stats()["abandoned"] > before["abandoned"] and time.time() < deadline:
        time.sleep(0.02)
    assert fetch.stack_stats()["abandoned"] == before["abandoned"]            # finished threads are forgotten


def test_a_fast_call_returns_its_value_and_a_failing_one_raises_its_error():
    assert fetch._run_with_deadline(lambda: 42, seconds=1) == 42
    with pytest.raises(ValueError):
        fetch._run_with_deadline(lambda: (_ for _ in ()).throw(ValueError("x")), seconds=1)


# ---------- L3: one definition of "needs a real browser" ----------

def page(status=200, html="", links=0, text=""):
    anchors = "".join(f"<a href='/{i}'>x</a>" for i in range(links))
    return {"url": "u", "final_url": "u", "status": status, "html": html or f"<html><body>{anchors}{text}</body></html>",
            "error": None}


def test_needs_browser_cases():
    assert fetch.needs_browser(page(status=403)) and fetch.needs_browser(page(status=429)) and fetch.needs_browser(page(status=503))
    assert not fetch.needs_browser(page(status=404))                              # not blocked, not usable: nothing to render
    assert not fetch.needs_browser({"status": 0, "html": "", "error": "x"})
    assert fetch.needs_browser(page(links=2))                                     # hardly any links
    assert not fetch.needs_browser(page(links=8, text="short"))                   # quick check: plenty of links
    assert fetch.needs_browser(page(links=8, text="short"), thorough=True)        # thorough: too little text
    assert not fetch.needs_browser(page(links=8, text="word " * 600), thorough=True)
    shell = page(links=8, html="<html><body><div id=\"root\"></div>" + "<a href='/x'>x</a>" * 8 + "word " * 600 + "</body></html>")
    assert fetch.needs_browser(shell, thorough=True) and not fetch.needs_browser(shell)


def test_discovery_and_scraper_share_the_same_definition():
    assert discovery.BLOCKED_STATUS is fetch.BLOCKED_STATUS
    assert not hasattr(scraper, "BLOCKED_STATUS") and not hasattr(scraper, "_needs_browser")
    assert not hasattr(discovery, "_needs_browser")


# ---------- L4: Runner state is only touched under its lock ----------

def test_snapshots_stay_valid_while_workers_change_state():
    runner = Runner()
    runner.queue([f"c{n}.com" for n in range(20)])
    stop = threading.Event()
    errors = []

    def writer(domain):
        n = 0
        while not stop.is_set():
            runner._update(domain, jobs_count=n, notes=[str(n)] * (n % 5))
            runner._bump(domain, new_count=1)
            runner.emit("jobs", domain=domain)
            n += 1

    def reader():
        try:
            while not stop.is_set():
                for company in runner.snapshot()["companies"]:
                    assert isinstance(company["notes"], list)
                runner.events_since(0)
        except Exception as error:                 # e.g. "dictionary changed size during iteration"
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(f"c{n}.com",)) for n in range(6)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    [t.start() for t in threads]
    time.sleep(0.5)
    stop.set()
    [t.join(5) for t in threads]
    assert not errors


def test_a_snapshot_is_a_copy_that_later_changes_cannot_reach():
    runner = Runner()
    runner.queue(["a.com"])
    snap = runner.snapshot()
    runner._update("a.com", state="done", notes=["late"])
    assert snap["companies"][0]["state"] == "ready" and snap["companies"][0]["notes"] == []
