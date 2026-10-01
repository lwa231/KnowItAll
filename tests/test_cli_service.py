import json

import pytest

import main as cli
from knowitall import paths, runner as runner_module, store
from knowitall.normalize import make_job
from knowitall.service import Service


def job(n):
    return make_job("Acme", f"Role {n}", f"https://acme.com/jobs/{n}", location="Austin, TX",
                    workplace="hybrid", source="greenhouse")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A private data dir, exports dir and database; the process cwd is restored afterwards."""
    monkeypatch.chdir(tmp_path)                         # registers the restore for enter_data_dir()'s chdir
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "data" / "cache")
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "data" / "logs")
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "data" / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    monkeypatch.setattr(paths, "legacy_dir", lambda: tmp_path / "nothing-here")
    yield tmp_path
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


def fake_find_jobs(monkeypatch, jobs_by_domain):
    def find_jobs(ctx, url, on_jobs=None):
        domain = url.replace("https://", "").rstrip("/")
        jobs = jobs_by_domain.get(domain, [])
        if jobs and on_jobs:
            on_jobs(jobs)
        return {"company": domain.split(".")[0].title(), "domain": domain, "input": url,
                "careers_pages": [f"https://{domain}/careers"], "source": "greenhouse",
                "source_detail": "greenhouse board 'x'", "notes": [], "jobs": jobs, "stopped": ctx.should_stop()}
    monkeypatch.setattr(runner_module, "find_jobs", find_jobs)


def test_cli_scan_records_history_marks_new_and_exports_like_the_window(sandbox, monkeypatch, capsys):
    fake_find_jobs(monkeypatch, {"acme.com": [job(1), job(2)]})
    assert cli.main(["acme.com", "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "Role 1" in out and "(2 new)" in out and "[hybrid]" in out
    assert [r["status"] for r in store.history()] == ["done"]                 # recorded in the same history
    assert (sandbox / "exports" / "jobs_acme.com.csv").exists()                # and exported to the same place
    assert store.query_jobs({"q": "role"})["total"] == 2                       # and searchable straight away
    assert cli.main(["acme.com", "--no-browser"]) == 0                         # a second scan: nothing new
    assert "(2 new)" not in capsys.readouterr().out


def test_no_history_flag_records_nothing_and_no_export_writes_nothing(sandbox, monkeypatch, capsys):
    fake_find_jobs(monkeypatch, {"acme.com": [job(1)]})
    assert cli.main(["acme.com", "--no-browser", "--no-history", "--no-export"]) == 0
    assert "Role 1" in capsys.readouterr().out
    assert not (sandbox / "data" / "history.db").exists()                       # the database was never even created
    assert not (sandbox / "exports").exists() or not list((sandbox / "exports").iterdir())


def test_exit_codes(sandbox, monkeypatch, capsys):
    fake_find_jobs(monkeypatch, {"acme.com": [job(1)], "empty.com": []})
    assert cli.main(["empty.com", "--no-browser", "--no-history", "--no-export"]) == 1     # ran, found nothing
    assert cli.main(["not a domain", "--no-browser", "--no-history"]) == 2                 # nothing to scan
    assert "nothing to scan" in capsys.readouterr().err


def test_cli_options_reach_the_scan_config(sandbox, monkeypatch):
    seen = []

    def find_jobs(ctx, url, on_jobs=None):
        seen.append(ctx.config)
        return {"company": "X", "domain": "x.com", "input": url, "careers_pages": [], "source": None,
                "notes": [], "jobs": [], "stopped": False}
    monkeypatch.setattr(runner_module, "find_jobs", find_jobs)
    cli.main(["x.com", "--no-browser", "--no-history", "--no-cache", "--max-jobs", "77", "--max-enrich", "9"])
    (config,) = seen
    assert (config.cache, config.max_jobs, config.max_enrich, config.use_browser) == ("REFRESH", 77, 9, False)


def test_cli_scans_several_companies_and_prints_each(sandbox, monkeypatch, capsys):
    fake_find_jobs(monkeypatch, {"a.com": [job(1)], "b.com": [job(2), job(3)]})
    assert cli.main(["a.com", "b.com", "--no-browser", "--no-history", "--no-export", "--parallel", "2", "--show", "1"]) == 0
    out = capsys.readouterr().out
    assert "== A (a.com) ==" in out and "== B (b.com) ==" in out and "... and 1 more" in out


# ---------- the service both front ends share ----------

def test_service_state_and_exports(sandbox, monkeypatch):
    fake_find_jobs(monkeypatch, {"acme.com": [job(1), job(2)]})
    (sandbox / "data").mkdir(exist_ok=True)
    store.init()                                          # the window's server and the CLI both do this at start
    service = Service()
    service.runner.set_browser(None, use_browser=False)
    assert service.start(["acme.com"], {"autosave": True})
    assert service.wait(5)
    state = service.state()
    assert state["companies"][0]["jobs_count"] == 2 and state["running"] is False
    events = service.state(since=state["cursor"])
    assert events["events"] == []                                               # nothing new since the last poll
    listing = service.list_exports()
    assert {f["name"] for f in listing} == {"jobs_acme.com.json", "jobs_acme.com.csv"}
    assert all(f["rows"] == 2 for f in listing)
    result = service.export_all()
    assert result["rows"] == 2 and result["written"].endswith("jobs_all_companies.csv")
    assert service.history()["runs"][0]["jobs_count"] == 2
    assert len(service.run_jobs(service.history()["runs"][0]["id"])["jobs"]) == 2
    assert service.query_jobs({"q": "role"})["total"] == 2
    assert service.jobs_of("acme.com")[0]["title"].startswith("Role")


def test_export_all_with_nothing_scanned(sandbox):
    assert Service().export_all() == {"written": None, "rows": 0}
    assert Service().list_exports() == []


def test_open_exports_uses_the_platform_opener(sandbox, monkeypatch):
    opened = []
    monkeypatch.setattr("knowitall.service.subprocess.Popen", lambda cmd: opened.append(cmd))
    monkeypatch.setattr("knowitall.service.sys.platform", "darwin")
    assert Service().open_exports() is True
    assert opened and opened[0][0] == "open" and opened[0][1].endswith("exports")
    monkeypatch.setattr("knowitall.service.subprocess.Popen", lambda cmd: (_ for _ in ()).throw(OSError("no opener")))
    assert Service().open_exports() is False


def test_stop_by_domain_is_available_through_http(app):
    import json as _json
    status, body, _ = app.call("POST", "/api/stop", {"domain": "nobody.com"}, app.json)
    assert status == 200 and _json.loads(body) == {"stopped": False}        # nothing by that name was running (audit M9c)
