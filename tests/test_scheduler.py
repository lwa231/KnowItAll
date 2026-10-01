"""Phase 2: the runner admits work while it runs (slots, Auto/Manual), and Stop is immediate."""
import json
import threading
import time

import pytest

from knowitall import fetch, runner as runner_module, store
from knowitall.discovery import parse_site
from knowitall.context import CancelToken, RunConfig, ScanContext
from knowitall.normalize import make_job
from knowitall.runner import Runner, clean_options
from knowitall.service import Service


def wait_until(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def states(runner):
    return {c["domain"]: c["state"] for c in runner.snapshot()["companies"]}


def result_for(url, ctx, jobs=()):
    return {"domain": url, "jobs": list(jobs), "notes": [], "stopped": ctx.should_stop()}


@pytest.fixture
def gates(monkeypatch):
    """Each company's fake scan blocks until its gate is opened: gates['a.com'].set()."""
    table = {}
    started = []

    def find_jobs(ctx, url, on_jobs=None):
        domain = parse_site(url).domain
        gate = table.setdefault(domain, threading.Event())
        started.append(domain)
        gate.wait(10)
        return result_for(domain, ctx)
    monkeypatch.setattr(runner_module, "find_jobs", find_jobs)
    table["started"] = started
    return table


def gate(gates, domain):
    return gates.setdefault(domain, threading.Event())


OPTS = {"autosave": False, "history": False}


# ---------- Auto: slots ----------

def test_four_addresses_two_slots_two_scan_two_wait_in_order(gates):
    runner = Runner()
    runner.set_parallel(2)
    runner.submit(["a.com", "b.com", "c.com", "d.com"], options=OPTS)
    assert wait_until(lambda: states(runner)["a.com"] == "scanning" and states(runner)["b.com"] == "scanning")
    assert states(runner) == {"a.com": "scanning", "b.com": "scanning", "c.com": "waiting", "d.com": "waiting"}
    ahead = {c["domain"]: c["waiting_ahead"] for c in runner.snapshot()["companies"]}
    assert (ahead["c.com"], ahead["d.com"]) == (0, 1)
    assert runner.running
    gate(gates, "a.com").set()                                   # a slot frees: the next in line starts by itself
    assert wait_until(lambda: states(runner)["c.com"] == "scanning")
    assert states(runner)["d.com"] == "waiting" and runner.snapshot()["companies"][3]["waiting_ahead"] == 0
    for d in ("b.com", "c.com", "d.com"):
        gate(gates, d).set()
    assert runner.wait(5) and set(states(runner).values()) == {"done"} and not runner.running


def test_raising_the_number_at_once_starts_waiting_companies_immediately(gates):
    runner = Runner()
    runner.set_parallel(2)
    runner.submit(["a.com", "b.com", "c.com", "d.com"], options=OPTS)
    assert wait_until(lambda: list(states(runner).values()).count("scanning") == 2)
    runner.set_parallel(3)
    assert wait_until(lambda: states(runner)["c.com"] == "scanning")
    assert states(runner)["d.com"] == "waiting"
    runner.set_parallel(1)                                       # lowering lets the running ones finish, nothing is cut
    assert list(states(runner).values()).count("scanning") == 3
    for d in ("a.com", "b.com", "c.com"):
        gate(gates, d).set()
    assert wait_until(lambda: states(runner)["d.com"] == "scanning")
    gate(gates, "d.com").set()
    assert runner.wait(5)


def test_parallel_must_be_1_to_3():
    runner = Runner()
    for bad in (0, 4, "2", True, None):
        with pytest.raises(ValueError):
            runner.set_parallel(bad)
    assert runner.parallel == 3


def test_a_company_added_during_a_run_is_scheduled_not_left_queued(gates):
    runner = Runner()
    runner.set_parallel(1)
    runner.submit(["a.com"], options=OPTS)
    assert wait_until(lambda: states(runner)["a.com"] == "scanning")
    runner.submit(["b.com"], options=OPTS)                        # the old "+ Queue" left this until the next Start
    assert states(runner)["b.com"] == "waiting"
    gate(gates, "a.com").set()
    assert wait_until(lambda: states(runner)["b.com"] == "scanning")
    gate(gates, "b.com").set()
    assert runner.wait(5)


def test_a_duplicate_submit_is_ignored_and_a_finished_company_rescans(gates):
    runner = Runner()
    first = runner.submit(["a.com"], options=OPTS)
    assert first["added"] == ["a.com"]
    assert wait_until(lambda: states(runner)["a.com"] == "scanning")
    again = runner.submit(["A.com", "https://www.a.com/careers"], options=OPTS)
    assert again["added"] == [] and again["ignored"] == ["a.com", "a.com"]
    gate(gates, "a.com").set()
    assert runner.wait(5)
    gates["a.com"] = threading.Event()
    assert runner.submit(["a.com"], options=OPTS)["added"] == ["a.com"]   # finished: scanned again
    assert wait_until(lambda: states(runner)["a.com"] == "scanning" and gates["started"].count("a.com") == 2)
    gates["a.com"].set()
    assert runner.wait(5)


def test_invalid_addresses_are_reported_not_fatal(gates):
    runner = Runner()
    result = runner.submit(["nonsense", "ok.com"], options=OPTS)
    assert result["invalid"] == ["nonsense"] and result["added"] == ["ok.com"]
    gate(gates, "ok.com").set()
    assert runner.wait(5)


# ---------- Manual ----------

def test_manual_adds_as_ready_and_runs_nothing_until_start(gates):
    runner = Runner()
    runner.submit(["a.com", "b.com"], start=False, options=OPTS)
    time.sleep(0.15)
    assert states(runner) == {"a.com": "ready", "b.com": "ready"} and not runner.running and gates["started"] == []
    assert runner.start_ready() == 2
    assert wait_until(lambda: len(gates["started"]) == 2)
    for d in ("a.com", "b.com"):
        gate(gates, d).set()
    assert runner.wait(5)
    assert runner.start_ready() == 0


def test_stop_leaves_ready_companies_alone(gates):
    runner = Runner()
    runner.submit(["a.com"], start=False, options=OPTS)
    assert runner.stop() is True and states(runner)["a.com"] == "ready"
    assert runner.stop("a.com") is False


# ---------- Stop ----------

def hang(monkeypatch, seconds=30):
    """A scan that ignores cancellation for a long time, like a request in flight."""
    release = threading.Event()

    def find_jobs(ctx, url, on_jobs=None):
        url = parse_site(url).domain
        on_jobs([make_job("X", "Early", f"https://{url}/jobs/early")])
        release.wait(seconds)
        on_jobs([make_job("X", "Late", f"https://{url}/jobs/late")])      # after Stop: must be discarded
        return result_for(url, ctx)
    monkeypatch.setattr(runner_module, "find_jobs", find_jobs)
    return release


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    store.init()
    yield store
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


def test_stop_finalizes_within_a_second_even_if_the_worker_is_stuck(db, monkeypatch):
    release = hang(monkeypatch)
    runner = Runner()
    runner.submit(["a.com", "b.com"], options={"autosave": False})
    assert wait_until(lambda: all(c["jobs_count"] == 1 for c in runner.snapshot()["companies"]))
    began = time.time()
    assert runner.stop() is True
    assert runner.wait(1) and time.time() - began < 1
    companies = runner.snapshot()["companies"]
    assert {c["state"] for c in companies} == {"stopped"} and {c["outcome"] for c in companies} == {"stopped"}
    assert all(c["outcome_detail"] == "Stopped. 1 posting found." for c in companies)
    assert {r["status"] for r in store.history()} == {"stopped"}
    release.set()                                                # the abandoned workers finish now...
    time.sleep(0.3)
    assert all(c["jobs_count"] == 1 for c in runner.snapshot()["companies"])       # ...and change nothing
    assert len(store.query_jobs({})["jobs"]) == 2                # the late rows were never saved
    assert {r["status"] for r in store.history()} == {"stopped"}


def test_stopping_one_company_frees_its_slot_for_the_next_in_line(db, monkeypatch):
    release = hang(monkeypatch)
    runner = Runner()
    runner.set_parallel(1)
    runner.submit(["a.com", "b.com"], options={"autosave": False})
    assert wait_until(lambda: states(runner)["a.com"] == "scanning")
    runner.stop("a.com")
    assert states(runner)["a.com"] == "stopped"
    assert wait_until(lambda: states(runner)["b.com"] == "scanning")          # did not wait for a.com's stuck worker
    release.set()
    assert runner.wait(5)


def test_stopping_a_waiting_company_removes_it_from_the_line(gates):
    runner = Runner()
    runner.set_parallel(1)
    runner.submit(["a.com", "b.com", "c.com"], options=OPTS)
    assert wait_until(lambda: states(runner)["a.com"] == "scanning")
    runner.stop("b.com")
    assert states(runner)["b.com"] == "stopped"
    assert runner.snapshot()["companies"][2]["waiting_ahead"] == 0
    gate(gates, "a.com").set()
    assert wait_until(lambda: states(runner)["c.com"] == "scanning")
    gate(gates, "c.com").set()
    assert runner.wait(5) and gates["started"] == ["a.com", "c.com"]


def test_the_time_limit_cuts_a_stuck_company_off_on_time(db, monkeypatch):
    release = hang(monkeypatch)
    runner = Runner()
    began = time.time()
    runner.submit(["a.com"], options={"autosave": False, "time_limit": 0.4})
    assert runner.wait(3) and time.time() - began < 2
    company = runner.snapshot()["companies"][0]
    assert (company["state"], company["outcome"], company["jobs_count"]) == ("stopped", "timed_out", 1)
    release.set()


def test_a_run_started_right_after_another_finishes_is_not_overwritten(gates):
    runner = Runner()
    for round_ in range(20):
        domain = f"r{round_}.com"
        runner.submit([domain], options=OPTS)
        gate(gates, domain).set()
        assert runner.wait(5)
        runner.submit([f"s{round_}.com"], options=OPTS)           # immediately, while the previous worker is unwinding
        assert runner.running
        gate(gates, f"s{round_}.com").set()
        assert runner.wait(5) and not runner.running
    assert runner._active == set() and not runner._waiting


# ---------- cancellable waits ----------

def test_a_blocked_call_is_abandoned_when_stop_is_pressed():
    release = threading.Event()
    token = CancelToken()
    threading.Timer(0.2, token.cancel).start()
    began = time.time()
    with pytest.raises(fetch.Stopped):
        fetch._run_with_deadline(lambda: release.wait(10), None, token.is_set)
    assert time.time() - began < 1
    release.set()


def test_fetch_pages_returns_stopped_pages_when_stop_arrives_mid_batch(monkeypatch):
    token = CancelToken()
    release = threading.Event()
    monkeypatch.setattr(fetch, "_fetch_page", lambda batch, cache=None, parallel=None: release.wait(10))
    threading.Timer(0.2, token.cancel).start()
    began = time.time()
    pages = fetch.fetch_pages(ScanContext(cancel=token), [f"https://x.com/{n}" for n in range(9)])
    assert time.time() - began < 1.5 and [p["error"] for p in pages] == ["stopped"] * 9
    release.set()


def test_fetch_json_stops_too(monkeypatch):
    token = CancelToken()
    release = threading.Event()
    monkeypatch.setattr(fetch, "_fetch_json", lambda *a, **k: release.wait(10))
    threading.Timer(0.2, token.cancel).start()
    assert fetch.fetch_json(ScanContext(cancel=token), "https://x.com/api")["error"] == "stopped"
    release.set()


# ---------- options are validated (audit M9b) ----------

@pytest.mark.parametrize("bad", [{"max_jobs": "abc"}, {"max_jobs": -1}, {"max_jobs": 10**9}, {"max_enrich": 501},
                                 {"concurrency": 4}, {"time_limit_min": 2}, {"fresh": "yes"}, {"time_limit": -3}, [], "x"])
def test_bad_options_are_refused(bad):
    with pytest.raises(ValueError):
        clean_options(bad)


def test_good_options_pass_and_empty_ones_are_fine():
    assert clean_options(None) == {} and clean_options({"max_jobs": None}) == {}
    assert clean_options({"max_jobs": 5, "fresh": True, "time_limit_min": 5, "concurrency": 2}) == \
        {"max_jobs": 5, "fresh": True, "time_limit_min": 5, "concurrency": 2}


# ---------- Service and HTTP ----------

def test_service_scan_follows_the_saved_mode(tmp_path, gates):
    service = Service(settings_path=tmp_path / "s.json")
    service.runner.submit = lambda urls, start=True, options=None: {"added": urls, "ignored": [], "invalid": [], "start": start, "options": options}
    assert service.scan(["a.com"])["start"] is True and service.scan(["a.com"])["mode"] == "auto"
    service.update_settings({"parallel_mode": "manual", "max_jobs": 77})
    result = service.scan(["a.com"])
    assert result["start"] is False and result["mode"] == "manual" and result["options"]["max_jobs"] == 77


def test_changing_the_setting_applies_to_the_runner_at_once(tmp_path):
    service = Service(settings_path=tmp_path / "s.json")
    assert service.runner.parallel == 3
    service.update_settings({"concurrency": 1})
    assert service.runner.parallel == 1
    assert service.set_parallel(2) == {"parallel": 2} and service.get_settings()["concurrency"] == 2
    with pytest.raises(ValueError):
        service.set_parallel(9)


def test_the_http_api(app, monkeypatch):
    monkeypatch.setattr(runner_module, "find_jobs", lambda ctx, url, on_jobs=None: result_for(url, ctx))
    app.service.update_settings({"parallel_mode": "manual", "autosave": False})
    status, body, _ = app.call("POST", "/api/scan", {"urls": ["a.com", "nonsense"]}, app.json)
    data = json.loads(body)
    assert status == 200 and data["added"] == ["a.com"] and data["invalid"] == ["nonsense"] and data["mode"] == "manual"
    assert states(app.service.runner) == {"a.com": "ready"}
    status, body, _ = app.call("POST", "/api/start", {}, app.json)
    assert json.loads(body) == {"started": 1}
    assert app.service.runner.wait(5)
    status, body, _ = app.call("POST", "/api/parallel", {"n": 2}, app.json)
    assert status == 200 and json.loads(body) == {"parallel": 2}
    assert app.call("POST", "/api/parallel", {"n": 9}, app.json)[0] == 400
    assert app.call("POST", "/api/parallel", {}, app.json)[0] == 400
    assert app.call("POST", "/api/scan", {"urls": "a.com"}, app.json)[0] == 400
    assert app.call("POST", "/api/run", {"urls": ["b.com"], "options": {"max_jobs": "abc"}}, app.json)[0] == 400   # was a dropped connection
    assert json.loads(app.call("POST", "/api/stop", {"domain": "nobody.com"}, app.json)[1]) == {"stopped": False}
    assert app.call("POST", "/api/stop", {"domain": 5}, app.json)[0] == 400


def test_an_unexpected_error_is_a_500_not_a_dropped_connection(app, monkeypatch):
    monkeypatch.setattr(app.service, "system_info", lambda: 1 / 0)
    status, body, _ = app.call("GET", "/api/system", None, app.auth)
    assert status == 500 and "internal error" in json.loads(body)["error"]
    assert app.call("GET", "/api/settings", None, app.auth)[0] == 200             # and the server carries on
