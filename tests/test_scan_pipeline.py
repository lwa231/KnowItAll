import json
import threading
import time

import pytest

from knowitall import fetch, runner as runner_module, scraper, store
from knowitall.ats import MODULES, smartrecruiters, workday
from knowitall.ats_detect import Detection
from knowitall.context import CancelToken, RunConfig, ScanContext
from knowitall.discovery import Site
from knowitall.normalize import make_job
from knowitall.runner import Runner
from tests.conftest import load_fixture

SITE = Site(input_url="https://acme.com", root_url="https://acme.com/", host="acme.com",
            domain="acme.com", slug="acme", name="Acme")


def wait_until(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------- context ----------

def test_cancel_token_child_follows_parent_but_not_the_reverse():
    run = CancelToken()
    one, two = run.child(), run.child()
    one.cancel()
    assert one.is_set() and not two.is_set() and not run.is_set()
    run.cancel()
    assert two.is_set()


def test_run_config_is_immutable_and_copies_with_changes():
    config = RunConfig(max_jobs=10)
    with pytest.raises(Exception):
        config.max_jobs = 5
    assert config.with_(max_jobs=7).max_jobs == 7 and config.max_jobs == 10


def test_scan_context_log_reaches_the_sink_and_survives_a_broken_sink():
    seen = []
    ScanContext(log_sink=seen.append).log("hello")
    assert seen == ["hello"]

    def broken(message):
        raise RuntimeError("sink down")
    ScanContext(log_sink=broken).log("still fine")


def test_emit_is_a_no_op_without_a_listener_and_skips_empty_batches():
    ScanContext().emit([{"title": "x"}])
    got = []
    ctx = ScanContext(on_jobs=got.append)
    ctx.emit([])
    ctx.emit([1])
    assert got == [[1]]


# ---------- fetch honours Stop between batches ----------

def test_fetch_pages_stops_between_batches(monkeypatch):
    token = CancelToken()
    ctx = ScanContext(cancel=token)
    calls = []

    def fake(batch, cache=None, parallel=None):
        calls.append(list(batch))
        token.cancel()                                   # Stop pressed while the first batch is in flight
        return [{"url": u, "final_url": u, "status": 200, "html": "x", "error": None} for u in batch]

    monkeypatch.setattr(fetch, "_fetch_page", fake)
    pages = fetch.fetch_pages(ctx, [f"https://x.com/{i}" for i in range(20)], parallel=8)
    assert len(calls) == 1 and len(calls[0]) == 8         # only the batch already running finished
    assert len(pages) == 20
    assert [p["error"] for p in pages[8:]] == ["stopped"] * 12


def test_fetch_json_and_render_skip_when_already_stopped(monkeypatch):
    token = CancelToken()
    token.cancel()
    ctx = ScanContext(cancel=token, config=RunConfig(renderer=lambda url, cancel: pytest.fail("rendered")))
    monkeypatch.setattr(fetch, "_fetch_json", lambda *a, **k: pytest.fail("fetched"))
    assert fetch.fetch_json(ctx, "https://x.com/a")["error"] == "stopped"
    assert fetch.fetch_json_many(ctx, [{"url": "https://x.com/a"}] * 3)[2]["error"] == "stopped"
    assert fetch.render_page(ctx, "https://x.com/a")["error"] == "stopped"
    assert fetch.fetch_page(ctx, "https://x.com/a")["error"] == "stopped"


def test_render_page_passes_the_scans_cancel_to_the_renderer():
    got = {}

    def renderer(url, should_cancel):
        got["cancel"] = should_cancel
        return {"url": url, "final_url": url, "status": 200, "html": "<html/>", "error": None}

    ctx = ScanContext(config=RunConfig(renderer=renderer))
    fetch.render_page(ctx, "https://x.com/a")
    assert got["cancel"] == ctx.should_stop


def test_render_page_falls_back_gracefully_when_the_renderer_raises():
    def renderer(url, should_cancel):
        raise RuntimeError("chrome exploded")
    page = fetch.render_page(ScanContext(config=RunConfig(renderer=renderer)), "https://x.com/a")
    assert page["status"] == 0 and "browser fallback failed" in page["error"]


# ---------- readers stream page by page ----------

def test_workday_streams_every_page_as_it_arrives(fake_fetch):
    def page(offset):
        return {"total": 100, "jobPostings": [
            {"title": f"Job {offset + i}", "externalPath": f"/job/Austin/Job-{offset + i}_R{i}",
             "locationsText": "Austin, Texas", "postedOn": "Posted Today"} for i in range(20)]}

    responses = iter([page(o) for o in range(0, 100, 20)])
    fake_fetch(workday, {})
    import knowitall.ats.workday as module
    module.fetch_json = lambda ctx, url, method="GET", json=None: {"status": 200, "data": next(responses)}
    module.fetch_json_many = lambda ctx, specs, parallel=4: [{"status": 200, "data": next(responses)} for _ in specs]
    batches = []
    ctx = ScanContext(on_jobs=lambda jobs: batches.append(len(jobs)))
    detection = Detection("workday", "acme", extra={"tenant": "acme", "wd": "wd1", "site": "careers"})
    jobs = workday.fetch_jobs(ctx, detection, SITE)
    assert len(jobs) == 100
    assert batches == [20] * 5                              # one batch per page, not one big batch at the end


def test_workday_respects_max_jobs_while_streaming(fake_fetch):
    import knowitall.ats.workday as module
    module.fetch_json = lambda ctx, url, method="GET", json=None: {"status": 200, "data": {
        "total": 500, "jobPostings": [{"title": f"J{i}", "externalPath": f"/job/A/J{i}", "locationsText": "Austin, Texas"}
                                      for i in range(20)]}}
    module.fetch_json_many = lambda ctx, specs, parallel=4: [module.fetch_json(ctx, "") for _ in specs]
    got = []
    ctx = ScanContext(config=RunConfig(max_jobs=30), on_jobs=got.extend)
    jobs = workday.fetch_jobs(ctx, Detection("workday", "a", extra={"tenant": "a", "wd": "wd1", "site": "s"}), SITE)
    assert len(jobs) == 30 and len(got) == 30


def test_workday_stops_fetching_pages_after_stop():
    import knowitall.ats.workday as module
    token = CancelToken()
    ctx = ScanContext(cancel=token)
    calls = []

    def many(ctx_, specs, parallel=4):
        calls.append(len(specs))
        token.cancel()
        return [{"status": 200, "data": {"jobPostings": []}} for _ in specs]

    module.fetch_json = lambda ctx_, url, method="GET", json=None: {"status": 200, "data": {"total": 400, "jobPostings": []}}
    module.fetch_json_many = many
    workday.fetch_jobs(ctx, Detection("workday", "a", extra={"tenant": "a", "wd": "wd1", "site": "s"}), SITE)
    assert calls == [4]                                     # one group, then Stop is noticed


def test_smartrecruiters_streams_pages_and_returns_none_when_empty(monkeypatch):
    pages = iter([
        {"totalFound": 3, "content": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]},
        {"totalFound": 3, "content": [{"id": "3", "name": "C"}]},
    ])
    monkeypatch.setattr(smartrecruiters, "PAGE_SIZE", 2)
    monkeypatch.setattr(smartrecruiters, "fetch_json", lambda ctx, url: {"status": 200, "data": next(pages)})
    batches = []
    jobs = smartrecruiters.fetch_jobs(ScanContext(on_jobs=lambda b: batches.append(len(b))),
                                      Detection("smartrecruiters", "acme"), SITE)
    assert len(jobs) == 3 and batches == [2, 1]
    monkeypatch.setattr(smartrecruiters, "fetch_json", lambda ctx, url: {"status": 200, "data": {"totalFound": 0, "content": []}})
    assert smartrecruiters.fetch_jobs(ScanContext(), Detection("smartrecruiters", "ghost"), SITE) is None


# ---------- find_jobs: the sink ----------

def job(n, company="Acme"):
    return make_job(company, f"Role {n}", f"https://acme.com/jobs/{n}", location="Austin, TX", source="greenhouse")


@pytest.fixture
def pipeline(monkeypatch):
    """find_jobs with discovery/detection faked so only the streaming logic is under test."""
    home = {"url": "https://acme.com/", "final_url": "https://acme.com/", "status": 200, "html": "<html>x</html>", "error": None}
    candidate = {**home, "url": "https://acme.com/careers", "final_url": "https://acme.com/careers", "score": 50, "reasons": ["test"]}
    monkeypatch.setattr(scraper, "discover", lambda ctx, site: (home, [candidate]))
    monkeypatch.setattr(scraper, "detect", lambda pages, site: ([Detection("greenhouse", "acme")], set(), None))

    def install(fetch_jobs):
        monkeypatch.setitem(MODULES, "greenhouse", type("M", (), {"fetch_jobs": staticmethod(fetch_jobs),
                                                                 "board_name": staticmethod(lambda ctx, d: "Acme")}))
    return install


def test_batches_reach_on_jobs_immediately_and_duplicates_are_dropped(pipeline):
    def reader(ctx, detection, site):
        ctx.emit([job(1), job(2)])
        seen_by_ui = list(received)                         # what the UI has BEFORE the reader finishes
        assert len(seen_by_ui) == 1 and len(seen_by_ui[0]) == 2
        ctx.emit([job(2), job(3)])                          # job 2 again: dropped
        return [job(1), job(2), job(3), job(4)]             # job 4 was never streamed: emitted at the end
    pipeline(reader)
    received = []
    result = scraper.find_jobs(ScanContext(), "acme.com", on_jobs=received.append)
    assert [len(b) for b in received] == [2, 1, 1]
    assert [j["title"] for j in result["jobs"]] == ["Role 1", "Role 2", "Role 3", "Role 4"]
    assert result["source"] == "greenhouse"


def test_big_batches_are_split_for_the_ui(pipeline):
    pipeline(lambda ctx, d, s: [job(n) for n in range(60)])
    received = []
    scraper.find_jobs(ScanContext(), "acme.com", on_jobs=received.append)
    assert [len(b) for b in received] == [25, 25, 10]


def test_rows_without_title_or_url_never_reach_the_ui(pipeline):
    bad = [{"title": None, "url": "https://acme.com/x"}, {"title": "No link", "url": None}]
    pipeline(lambda ctx, d, s: [job(1)] + bad)
    received = []
    result = scraper.find_jobs(ScanContext(), "acme.com", on_jobs=received.append)
    assert len(result["jobs"]) == 1 and sum(len(b) for b in received) == 1


def test_company_name_is_fixed_per_batch_from_the_feed(pipeline):
    pipeline(lambda ctx, d, s: [job(1, company="Acme, Inc."), job(2, company="Acme, Inc.")])
    result = scraper.find_jobs(ScanContext(), "acme.com", on_jobs=lambda b: None)
    assert {j["company"] for j in result["jobs"]} == {"Acme, Inc."} and result["company"] == "Acme, Inc."


def test_a_guessed_board_that_is_rejected_leaks_no_rows(monkeypatch):
    emitted = []

    def reader(ctx, detection, site):
        ctx.emit([job(1), job(2)])                          # a probe reader streams like any other
        return [job(1), job(2)]

    monkeypatch.setitem(MODULES, "greenhouse", type("M", (), {
        "fetch_jobs": staticmethod(reader), "board_name": staticmethod(lambda ctx, d: "Totally Different Inc")}))
    monkeypatch.setattr(scraper, "GUESSABLE", ["greenhouse"])
    other = Site("https://acme.com", "https://acme.com/", "acme.com", "acme.com", "acme", "Acme")
    ctx = ScanContext(on_jobs=emitted.append)
    foreign = [make_job("X", "T", "https://elsewhere.io/1")]
    monkeypatch.setitem(MODULES, "greenhouse", type("M", (), {
        "fetch_jobs": staticmethod(lambda c, d, s: (c.emit(foreign), foreign)[1]),
        "board_name": staticmethod(lambda c, d: "Totally Different Inc")}))
    detection, jobs = scraper._guess_boards(ctx, other)
    assert (detection, jobs) == (None, None)
    assert emitted == []                                    # nothing was shown to the user


def test_stop_before_discovery_finishes_returns_stopped(monkeypatch):
    token = CancelToken()
    monkeypatch.setattr(scraper, "discover", lambda ctx, site: (token.cancel(), ({"status": 200}, []))[1])
    result = scraper.find_jobs(ScanContext(cancel=token), "acme.com")
    assert result["stopped"] is True and result["jobs"] == []


# ---------- Runner ----------

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


def fake_scan(monkeypatch, behaviour):
    def find_jobs(ctx, url, on_jobs=None):
        return behaviour(ctx, url, on_jobs)
    monkeypatch.setattr(runner_module, "find_jobs", find_jobs)


def result_for(url, jobs, ctx):
    return {"company": url.split(".")[0].title(), "domain": url, "input": url, "careers_pages": [f"https://{url}/careers"],
            "source": "greenhouse", "notes": [], "jobs": jobs, "stopped": ctx.should_stop()}


def test_run_records_history_marks_new_and_finishes(db, monkeypatch, tmp_path):
    from knowitall import paths
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")

    def scan(ctx, url, on_jobs):
        jobs = [job(1), job(2)]
        on_jobs(jobs)
        return result_for(url, jobs, ctx)
    fake_scan(monkeypatch, scan)
    runner = Runner()
    assert runner.start(["acme.com"], {"concurrency": 1})
    assert runner.wait(5)
    company = runner.snapshot()["companies"][0]
    assert (company["state"], company["jobs_count"], company["new_count"]) == ("done", 2, 2)
    assert [r["status"] for r in store.history()] == ["done"]
    assert all(j["is_new"] for j in runner.jobs_of("acme.com"))
    assert (tmp_path / "exports" / "jobs_acme.com.csv").exists()          # autosaved


def test_second_scan_marks_nothing_new(db, monkeypatch, tmp_path):
    from knowitall import paths
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")
    fake_scan(monkeypatch, lambda ctx, url, on_jobs: (on_jobs([job(1)]), result_for(url, [job(1)], ctx))[1])
    runner = Runner()
    runner.start(["acme.com"]); runner.wait(5)
    runner.start([])                                    # "scan again"
    runner.wait(5)
    company = runner.snapshot()["companies"][0]
    assert company["jobs_count"] == 1 and company["new_count"] == 0


def test_history_off_leaves_the_database_alone_and_keeps_jobs_in_memory(db, monkeypatch):
    fake_scan(monkeypatch, lambda ctx, url, on_jobs: (on_jobs([job(1), job(2)]), result_for(url, [job(1), job(2)], ctx))[1])
    runner = Runner()
    runner.start(["acme.com"], {"history": False, "autosave": False})
    assert runner.wait(5)
    assert store.history() == []
    jobs = runner.jobs_of("acme.com")
    assert len(jobs) == 2 and not any(j["is_new"] for j in jobs)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 0


def test_stopping_one_company_leaves_the_others_running(db, monkeypatch):
    release = threading.Event()

    def scan(ctx, url, on_jobs):
        if "slow.com" in url:
            wait_until(ctx.should_stop, 5)
            on_jobs([job(9)])
        else:
            on_jobs([job(1)])
            release.wait(5)
        return result_for(url, [], ctx)
    fake_scan(monkeypatch, scan)
    runner = Runner()
    runner.start(["slow.com", "fast.com"], {"concurrency": 2, "autosave": False})
    assert wait_until(lambda: all(c["state"] == "scanning" for c in runner.snapshot()["companies"]))
    assert runner.stop("slow.com") is True
    assert wait_until(lambda: {c["domain"]: c["state"] for c in runner.snapshot()["companies"]}["slow.com"] == "stopped")
    states = {c["domain"]: c["state"] for c in runner.snapshot()["companies"]}
    assert states["fast.com"] == "scanning"                # untouched
    release.set()
    assert runner.wait(5)
    final = {c["domain"]: c["state"] for c in runner.snapshot()["companies"]}
    assert final == {"slow.com": "stopped", "fast.com": "done"}
    assert runner.stop("nobody.com") is False


def test_stop_all_cancels_every_company_and_keeps_partial_results(db, monkeypatch):
    def scan(ctx, url, on_jobs):
        on_jobs([job(1)])
        wait_until(ctx.should_stop, 5)
        return result_for(url, [], ctx)
    fake_scan(monkeypatch, scan)
    runner = Runner()
    runner.start(["a.com", "b.com"], {"concurrency": 2, "autosave": False})
    assert wait_until(lambda: all(c["jobs_count"] == 1 for c in runner.snapshot()["companies"]))
    runner.stop()
    assert runner.wait(5)
    companies = runner.snapshot()["companies"]
    assert {c["state"] for c in companies} == {"stopped"} and all(c["jobs_count"] == 1 for c in companies)
    assert {r["status"] for r in store.history()} == {"stopped"}


def test_queued_companies_are_marked_stopped_when_stop_comes_first(db, monkeypatch):
    gate = threading.Event()

    def scan(ctx, url, on_jobs):
        gate.wait(5)
        return result_for(url, [], ctx)
    fake_scan(monkeypatch, scan)
    runner = Runner()
    runner.start(["a.com", "b.com", "c.com"], {"concurrency": 1, "autosave": False})
    assert wait_until(lambda: runner.snapshot()["companies"][0]["state"] == "scanning")
    runner.stop()
    gate.set()
    assert runner.wait(5)
    assert [c["state"] for c in runner.snapshot()["companies"]] == ["stopped", "stopped", "stopped"]


def test_each_start_gets_its_own_config(db, monkeypatch):
    seen = []
    fake_scan(monkeypatch, lambda ctx, url, on_jobs: (seen.append(ctx.config), result_for(url, [], ctx))[1])
    runner = Runner()
    renderer = lambda url, cancel: None
    runner.set_browser(renderer, use_browser=True)
    runner.start(["a.com"], {"fresh": True, "max_jobs": 5, "max_enrich": 3, "autosave": False}); runner.wait(5)
    runner.start([], {"autosave": False}); runner.wait(5)
    runner.start([], {"use_browser": False, "autosave": False}); runner.wait(5)
    first, second, third = seen
    assert (first.cache, first.max_jobs, first.max_enrich, first.renderer) == ("REFRESH", 5, 3, renderer)
    assert (second.cache, second.max_jobs, second.max_enrich) == (True, 2000, 50)      # nothing leaked from the first
    assert third.use_browser is False and third.renderer is None


def test_second_start_while_running_is_refused(db, monkeypatch):
    gate = threading.Event()
    fake_scan(monkeypatch, lambda ctx, url, on_jobs: (gate.wait(5), result_for(url, [], ctx))[1])
    runner = Runner()
    assert runner.start(["a.com"], {"autosave": False}) is True
    assert runner.start(["b.com"]) is False
    assert "b.com" not in runner.snapshot()["companies"][0]["domain"]
    gate.set()
    runner.wait(5)


def test_invalid_input_starts_nothing_and_leaves_the_runner_usable(db, monkeypatch):
    fake_scan(monkeypatch, lambda ctx, url, on_jobs: result_for(url, [], ctx))
    runner = Runner()
    assert runner.start(["not a domain"]) is False
    assert runner.running is False
    assert runner.start(["ok.com"], {"autosave": False}) is True
    runner.wait(5)


def test_a_crashing_scan_is_recorded_as_failed_without_stopping_the_others(db, monkeypatch):
    def scan(ctx, url, on_jobs):
        if "bad.com" in url:
            on_jobs([job(1)])
            raise RuntimeError("boom")
        on_jobs([job(2)])
        return result_for(url, [job(2)], ctx)
    fake_scan(monkeypatch, scan)
    runner = Runner()
    runner.start(["bad.com", "good.com"], {"concurrency": 1, "autosave": False}); runner.wait(5)
    states = {c["domain"]: c for c in runner.snapshot()["companies"]}
    assert states["bad.com"]["state"] == "failed" and "boom" in states["bad.com"]["notes"][0]
    assert states["good.com"]["state"] == "done"
    assert sorted(r["status"] for r in store.history()) == ["done", "failed"]


def test_two_runners_do_not_share_state(db, monkeypatch):
    fake_scan(monkeypatch, lambda ctx, url, on_jobs: result_for(url, [], ctx))
    one, two = Runner(), Runner()
    one.queue(["a.com"])
    assert two.snapshot()["companies"] == []
    one.notice("x", "warning", "hi")
    assert two.snapshot()["notices"] == []
