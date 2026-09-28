import http.client
import json
import threading
import time

import pytest

from knowitall import server, settings as settings_store
from knowitall.browser import BrowserPool
from knowitall.runner import Runner
from knowitall.service import Service
from tests.test_browser_pool import FakeDriver


# ---------- settings ----------

def test_defaults_when_nothing_is_saved(tmp_path):
    assert settings_store.load(tmp_path / "nope.json") == settings_store.DEFAULTS


def test_round_trip_and_atomic_write(tmp_path):
    path = tmp_path / "settings.json"
    values = {**settings_store.DEFAULTS, "theme": "light", "max_jobs": 500, "fresh": True}
    settings_store.save(values, path)
    assert settings_store.load(path) == values
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]        # no temp file left behind


@pytest.mark.parametrize("content", ["", "not json", "[1,2,3]", "null", '{"theme": "neon", "max_jobs": "lots"}',
                                     '{"max_jobs": true, "concurrency": 99, "browser_workers": 0, "fresh": "yes"}'])
def test_damaged_or_hand_edited_files_fall_back_field_by_field(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content)
    loaded = settings_store.load(path)
    assert set(loaded) == set(settings_store.DEFAULTS)
    assert loaded["theme"] in ("dark", "light") and 1 <= loaded["concurrency"] <= 4
    assert loaded["max_jobs"] != True and isinstance(loaded["max_jobs"], int)
    assert isinstance(loaded["fresh"], bool) and 1 <= loaded["browser_workers"] <= 3


def test_out_of_range_numbers_are_clamped_when_loading(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"concurrency": 99, "browser_workers": 0, "max_jobs": 10**9, "max_enrich": -5}))
    loaded = settings_store.load(path)
    assert (loaded["concurrency"], loaded["browser_workers"], loaded["max_jobs"], loaded["max_enrich"]) == (4, 1, 100_000, 0)


def test_unknown_keys_are_dropped_on_save(tmp_path):
    settings_store.save({"theme": "dark", "evil": "x"}, tmp_path / "s.json")
    assert "evil" not in json.loads((tmp_path / "s.json").read_text())


@pytest.mark.parametrize("update", [
    {"theme": "neon"}, {"max_jobs": 0}, {"max_jobs": "5"}, {"max_jobs": True}, {"max_jobs": 1.5},
    {"concurrency": 5}, {"browser_workers": 4}, {"fresh": "yes"}, {"autosave": 1}, {"nonsense": 1}, [], "text", None,
])
def test_validate_rejects_bad_updates(update):
    with pytest.raises(ValueError):
        settings_store.validate(update)


def test_validate_accepts_partial_good_updates():
    assert settings_store.validate({"theme": "light", "browser_workers": 3}) == {"theme": "light", "browser_workers": 3}
    assert settings_store.validate({}) == {}


def test_service_merges_saves_and_applies_settings(tmp_path):
    service = Service(settings_path=tmp_path / "settings.json")
    pool = BrowserPool(size=1, driver_factory=lambda h: FakeDriver())
    service.attach_browser(pool)
    assert pool.size == 1
    result = service.update_settings({"browser_workers": 3, "theme": "light"})
    assert result["browser_workers"] == 3 and result["theme"] == "light" and result["max_jobs"] == 2000
    assert pool.size == 3                                                     # applied at once
    assert Service(settings_path=tmp_path / "settings.json").get_settings()["theme"] == "light"     # and persisted
    service.update_settings({"max_jobs": 10})
    assert service.get_settings()["theme"] == "light"                          # earlier values are kept
    with pytest.raises(ValueError):
        service.update_settings({"theme": "neon"})
    assert service.get_settings()["max_jobs"] == 10                            # a rejected update changes nothing
    pool.close()


def test_attaching_a_browser_applies_the_saved_worker_count(tmp_path):
    service = Service(settings_path=tmp_path / "settings.json")
    service.update_settings({"browser_workers": 2})
    pool = BrowserPool(size=1, driver_factory=lambda h: FakeDriver())
    service.attach_browser(pool)
    assert pool.size == 2
    service.attach_browser(None)
    assert service.runner.use_browser is False and service.runner.renderer is None
    pool.close()


def test_settings_endpoints(app, tmp_path, monkeypatch):
    app.service.settings_path = tmp_path / "settings.json"
    status, body, _ = app.call("GET", "/api/settings", headers=app.auth)
    assert status == 200 and json.loads(body) == settings_store.DEFAULTS
    assert app.call("GET", "/api/settings")[0] == 403
    status, body, _ = app.call("POST", "/api/settings", {"theme": "light", "max_jobs": 300}, app.json)
    assert status == 200 and json.loads(body)["theme"] == "light"
    assert json.loads(app.call("GET", "/api/settings", headers=app.auth)[1])["max_jobs"] == 300
    status, body, _ = app.call("POST", "/api/settings", {"theme": "neon"}, app.json)
    assert status == 400 and "theme" in json.loads(body)["error"]
    status, body, _ = app.call("POST", "/api/settings", {"unknown": 1}, app.json)
    assert status == 400
    assert app.call("POST", "/api/settings", {"theme": "dark"}, {"Content-Type": "application/json"})[0] == 403


# ---------- browser pool resizing ----------

def test_pool_grows_and_allows_more_parallel_renders():
    active, peak, lock = [0], [0], threading.Lock()

    class Slow(FakeDriver):
        def get(self, url, timeout=60):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.15)
            with lock:
                active[0] -= 1
            super().get(url, timeout)

    pool = BrowserPool(size=1, driver_factory=lambda h: Slow(), settle=0)
    assert pool.resize(3) == 3
    threads = [threading.Thread(target=pool.render, args=(f"https://x.com/{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert peak[0] == 3
    pool.close()


def test_shrinking_closes_idle_surplus_browsers_at_once():
    FakeDriver.created = []
    pool = BrowserPool(size=3, driver_factory=lambda h: FakeDriver(delay=0.1), settle=0)
    threads = [threading.Thread(target=pool.render, args=(f"https://x.com/{i}",)) for i in range(3)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert pool.active_drivers() == 3
    pool.resize(1)
    assert pool.active_drivers() == 1
    assert sum(d.closed for d in FakeDriver.created) == 2
    pool.close()


def test_shrinking_while_busy_closes_the_surplus_browser_when_its_render_ends():
    FakeDriver.created = []
    release = threading.Event()

    class Held(FakeDriver):
        def get(self, url, timeout=60):
            release.wait(5)
            super().get(url, timeout)

    pool = BrowserPool(size=2, driver_factory=lambda h: Held(), settle=0)
    threads = [threading.Thread(target=pool.render, args=(f"https://x.com/{i}",)) for i in range(2)]
    [t.start() for t in threads]
    deadline = time.time() + 3
    while len(FakeDriver.created) < 2 and time.time() < deadline:
        time.sleep(0.01)
    pool.resize(1)                                          # both are mid-render: nothing is cut off
    assert not any(d.closed for d in FakeDriver.created)
    release.set()
    [t.join(10) for t in threads]
    assert pool.active_drivers() == 1 and sum(d.closed for d in FakeDriver.created) == 1
    pool.close()


def test_resize_is_clamped_and_new_size_is_used_by_later_renders():
    pool = BrowserPool(size=1, driver_factory=lambda h: FakeDriver(), settle=0)
    assert pool.resize(0) == 1 and pool.resize(99) == 3 and pool.resize("2") == 2
    assert pool.render("https://x.com/1")["status"] == 200
    pool.close()


# ---------- system info ----------

def test_system_info_shape_with_and_without_a_browser(tmp_path):
    service = Service(settings_path=tmp_path / "s.json")
    off = service.system_info()
    assert off["browser"]["enabled"] is False and off["browser"]["workers"] == 0 and off["browser"]["reason"]
    pool = BrowserPool(size=2, driver_factory=lambda h: FakeDriver(), settle=0)
    service.attach_browser(pool)
    on = service.system_info()
    assert on["browser"] == {"enabled": True, "available": True, "reason": None, "workers": 1, "active": 0}
    pool.render("https://x.com/1")
    assert service.system_info()["browser"]["active"] == 1
    assert {"botasaurus", "requests", "timeouts", "abandoned"} <= set(on["requests"])
    assert {"cache_bytes", "database_bytes", "logs_bytes", "exports_bytes"} <= set(on["usage"])
    assert {"data", "exports", "database", "cache", "logs"} <= set(on["paths"])
    assert on["versions"]["tested_with"] and "running" in on
    pool.close()


def test_system_info_reports_why_chrome_is_unavailable(tmp_path):
    def broken(headless):
        raise FileNotFoundError("no chrome")
    service = Service(settings_path=tmp_path / "s.json")
    pool = BrowserPool(size=1, driver_factory=broken)
    service.attach_browser(pool)
    pool.render("https://x.com/1")
    info = service.system_info()["browser"]
    assert info["available"] is False and "Chrome could not be started" in info["reason"]


def test_system_endpoint(app):
    status, body, _ = app.call("GET", "/api/system", headers=app.auth)
    assert status == 200 and {"browser", "requests", "usage", "paths", "versions"} <= set(json.loads(body))
    assert app.call("GET", "/api/system")[0] == 403


# ---------- runner events ----------

def test_job_events_carry_counts_not_rows(tmp_path, monkeypatch):
    from knowitall import runner as runner_module, store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    store.init()
    from knowitall.normalize import make_job

    def scan(ctx, url, on_jobs=None):
        on_jobs([make_job("A", f"R{n}", f"https://a.com/{n}") for n in range(3)])
        return {"company": "A", "domain": "a.com", "input": url, "careers_pages": [], "source": "x", "notes": [],
                "jobs": [], "stopped": False}
    monkeypatch.setattr(runner_module, "find_jobs", scan)
    runner = Runner()
    runner.start(["a.com"], {"autosave": False})
    assert runner.wait(5)
    jobs_events = [e for e in runner.events_since(0)[0] if e["kind"] == "jobs"]
    assert jobs_events == [{"i": jobs_events[0]["i"], "kind": "jobs", "domain": "a.com", "added": 3, "total": 3}]
    store._local.conn.close(); store._local.conn = None


def test_wait_for_events_blocks_until_something_happens():
    runner = Runner()
    runner.emit("log", text="old")
    _, cursor = runner.events_since(0)
    started = time.time()
    events, _ = runner.wait_for_events(cursor, timeout=0.2)
    assert events == [] and 0.15 <= time.time() - started < 1.0                # timed out, nothing new
    threading.Timer(0.1, lambda: runner.emit("log", text="new")).start()
    started = time.time()
    events, new_cursor = runner.wait_for_events(cursor, timeout=5)
    assert [e["text"] for e in events] == ["new"] and new_cursor == cursor + 1 and time.time() - started < 2
    events, _ = runner.wait_for_events(0, timeout=5)                          # already-available events return at once
    assert len(events) == 2


# ---------- the event stream ----------

class Stream:
    """A client of GET /api/stream reading server-sent messages."""

    def __init__(self, app, since=0, headers=None):
        self.conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
        self.conn.request("GET", f"/api/stream?since={since}", headers=headers if headers is not None else app.auth)
        self.response = self.conn.getresponse()

    def message(self):
        """The next data message as a dict, or the string 'ping' for a heartbeat."""
        while True:
            line = self.response.fp.readline()
            if not line:
                return None
            line = line.decode().strip()
            if line.startswith("data: "):
                return json.loads(line[6:])
            if line.startswith(":"):
                return "ping"

    def close(self):
        self.conn.close()


def test_stream_sends_the_current_state_at_once_then_pushes_events(app):
    runner = app.service.runner
    stream = Stream(app)
    assert stream.response.status == 200
    assert stream.response.getheader("Content-Type").startswith("text/event-stream")
    first = stream.message()
    assert {"running", "companies", "events", "cursor", "notices"} <= set(first)
    runner.log("hello from the runner")
    second = stream.message()
    assert [e["text"] for e in second["events"] if e["kind"] == "log"] == ["hello from the runner"]
    assert second["cursor"] > first["cursor"]
    runner.queue(["streamtest.com"])
    third = stream.message()
    assert any(c["domain"] == "streamtest.com" for c in third["companies"])
    stream.close()


def test_stream_resumes_from_a_cursor_without_repeating_events(app):
    runner = app.service.runner
    for n in range(5):
        runner.log(f"line {n}")
    _, cursor = runner.events_since(0)
    stream = Stream(app, since=cursor)
    first = stream.message()
    assert first["events"] == []                                # nothing after the cursor yet
    runner.log("after")
    assert [e["text"] for e in stream.message()["events"]] == ["after"]
    stream.close()


def test_a_burst_of_events_arrives_together(app):
    runner = app.service.runner
    stream = Stream(app)
    stream.message()
    for n in range(20):
        runner.log(f"burst {n}")
    seen, deadline = [], time.time() + 3
    while len(seen) < 20 and time.time() < deadline:
        seen += [e["text"] for e in stream.message()["events"] if e["kind"] == "log"]
    assert seen == [f"burst {n}" for n in range(20)]
    stream.close()


def test_idle_stream_sends_heartbeats(app, monkeypatch):
    monkeypatch.setattr(server, "HEARTBEAT_SECONDS", 0.2)
    stream = Stream(app)
    stream.message()
    assert stream.message() == "ping"
    stream.close()


def test_stream_needs_the_token_and_a_sane_cursor(app):
    denied = Stream(app, headers={})
    assert denied.response.status == 403
    denied.close()
    for route in ("/api/stream?since=abc", "/api/state?since=abc", "/api/run-jobs?id=x"):
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
        conn.request("GET", route, headers=app.auth)
        assert conn.getresponse().status == 400, route
        conn.close()


def test_stream_ends_when_the_server_stops(app):
    stream = Stream(app)
    stream.message()
    threading.Timer(0.1, lambda: app.httpd.stopping.set()).start()
    assert stream.message() is None                              # the connection is closed by the server
    stream.close()


def test_a_client_that_disconnects_does_not_break_the_server(app):
    stream = Stream(app)
    stream.message()
    stream.close()
    app.service.runner.log("nobody is listening")
    time.sleep(0.2)
    assert app.call("GET", "/api/state", headers=app.auth)[0] == 200
