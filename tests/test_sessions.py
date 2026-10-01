"""Phase 5A: migration v4, sessions, interrupted runs, and the closing rule's 24-hour gap."""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from knowitall import store

ROOT = Path(__file__).resolve().parent.parent


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


def job(url, title="Engineer"):
    return {"company": "Acme", "title": title, "url": url, "location": "Berlin", "remote": None,
            "department": None, "posted": None, "source": "lever"}


def scan(domain, urls, session=None, status="done", complete=True):
    run = store.start_run(domain, domain, session)
    batch = [job(u) for u in urls]
    new = store.mark_new(batch, domain)
    store.save_jobs(run, batch)
    return run, store.finish_run(run, status, "lever", len(batch), new, complete=complete)


def titles(**filters):
    return {j["url"].rsplit("/", 1)[-1] for j in store.query_jobs(filters)["jobs"]}


# ---------- the migration ----------

def make_v3(path):
    conn = store.connect()
    for version, migrate in store.MIGRATIONS[:3]:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        migrate(conn)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.execute("INSERT INTO runs (domain, company, status, started_at, finished_at, jobs_count) VALUES ('a.com', 'A', 'done', 'x', 'y', 2)")
    conn.execute("INSERT INTO postings (url_key, domain, title, url, last_run_id, first_seen) VALUES ('a.com/1', 'a.com', 'T', 'https://a.com/1', 1, 'x')")
    conn.commit()


def test_v3_becomes_v4_keeping_all_data_and_backing_up(db, tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setattr(store, "DB_PATH", path)
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    make_v3(path)
    store._local.conn.close(); store._local.conn = None
    store.init()
    conn = store.connect()
    assert store.schema_version() == 4
    assert {"session_id", "outcome", "outcome_detail"} <= {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 1 and conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert conn.execute("SELECT session_id FROM runs").fetchone()[0] is None          # old runs belong to no session
    assert "sessions" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert not path.with_name("old.db.v3.bak").exists()                                # additive migration: no rewrite, no backup needed


def test_two_processes_initialising_the_same_v3_database_both_succeed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "KNOWITALL_HOME": str(home), "KNOWITALL_LEGACY_DIR": str(tmp_path / "none"), "PYTHONPATH": str(ROOT)}
    setup = ("from knowitall import store\nconn = store.connect()\n"
             "for v, m in store.MIGRATIONS[:3]:\n"
             "    conn.execute('BEGIN IMMEDIATE') if not conn.in_transaction else None\n"
             "    conn.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)')\n"
             "    m(conn); conn.execute('INSERT INTO schema_version VALUES (?)', (v,))\n"
             "for i in range(3000):\n"
             "    conn.execute('INSERT INTO postings (url_key, domain, title, url, first_seen) VALUES (?, ?, ?, ?, ?)', (f'a.com/{i}', 'a.com', 'T', 'u', 'x'))\n"
             "conn.commit()\n")
    subprocess.run([sys.executable, "-c", setup], env=env, check=True, cwd=ROOT)
    code = "from knowitall import store\nstore.init()\nprint(store.schema_version())\n"
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(3)]
    results = [p.communicate(timeout=60) + (p.returncode,) for p in procs]
    assert all(rc == 0 and out.strip() == "4" for out, err, rc in results), results


# ---------- sessions ----------

def test_a_session_is_recorded_and_ended(db):
    session = store.start_session("0.1")
    assert store.session_started(session)
    store.end_session(session)
    row = store.connect().execute("SELECT * FROM sessions WHERE id = ?", (session,)).fetchone()
    assert row["app_version"] == "0.1" and row["ended_at"]


def test_history_can_be_limited_to_one_session_and_carries_the_outcome(db):
    first, second = store.start_session(), store.start_session()
    scan("old.com", ["https://old.com/1"], first)
    run, _ = scan("new.com", ["https://new.com/1"], second)
    assert [r["domain"] for r in store.history(session_id=second)] == ["new.com"]
    assert len(store.history()) == 2
    store.finish_run(run, "done", "lever", 1, 1, outcome="found", outcome_detail="1 posting")
    row = store.history(session_id=second)[0]
    assert (row["outcome"], row["outcome_detail"]) == ("found", "1 posting")


def test_the_feed_after_a_restart_shows_no_companies_until_one_is_scanned(db):
    earlier = store.start_session()
    scan("old.com", ["https://old.com/a"], earlier)
    now = store.start_session()
    assert titles(session_id=now) == set()
    assert titles() == {"a"}                                            # the database still has it (new/closed need that)
    scan("new.com", ["https://new.com/b"], now)
    assert titles(session_id=now) == {"b"}


def test_the_newest_run_per_company_is_chosen_within_the_session(db):
    earlier, now = store.start_session(), store.start_session()
    scan("x.com", ["https://x.com/old"], earlier)
    scan("x.com", ["https://x.com/mine"], now)
    assert titles(session_id=now) == {"mine"}


def test_gone_and_closed_views_cover_only_companies_scanned_this_session(db, monkeypatch):
    monkeypatch.setattr(store, "CLOSE_AFTER", timedelta(0))
    earlier = store.start_session()
    scan("a.com", ["https://a.com/1", "https://a.com/2"], earlier)
    scan("a.com", ["https://a.com/2"], earlier)
    scan("b.com", ["https://b.com/1", "https://b.com/2"], earlier)
    scan("b.com", ["https://b.com/2"], earlier)
    now = store.start_session()
    assert titles(status="missing") == {"1"} and len(store.query_jobs({"status": "missing"})["jobs"]) == 2
    scan("a.com", ["https://a.com/2"], now)
    assert {j["url"] for j in store.query_jobs({"status": "closed", "session_id": now})["jobs"]} == {"https://a.com/1"}


def test_a_session_id_cannot_be_used_to_reach_other_data(db):
    a = store.start_session()
    scan("x.com", ["https://x.com/1"], a)
    assert titles(session_id="' OR 1=1 --") == set()                     # a value, never SQL


# ---------- interrupted runs (audit H5b) ----------

def test_a_run_left_running_by_a_killed_app_becomes_interrupted(db):
    crashed = store.start_session()
    orphan = store.start_run("dead.com", "Dead", crashed)
    store.save_jobs(orphan, [job("https://dead.com/1")])
    now = store.start_session()
    live = store.start_run("live.com", "Live", now)
    assert store.mark_interrupted(now) == 1
    status = {r["id"]: r["status"] for r in store.history()}
    assert status[orphan] == "interrupted" and status[live] == "running"
    assert titles(session_id=now) == set()
    assert store.query_jobs({})["total"] == 0                           # an interrupted run is not "current" any more


def test_interrupted_runs_are_cleaned_up_by_pruning(db):
    old = store.start_session()
    for n in range(3):
        run = store.start_run("d.com", "D", old)
        store.finish_run(run, "done", "x", 0, 0)
    orphan = store.start_run("d.com", "D", old)
    store.mark_interrupted(store.start_session())
    store.prune(keep_runs=1)
    assert [r["status"] for r in store.history()] == ["interrupted"]      # 'running' rows were never pruned; interrupted ones are


# ---------- the closing rule (audit H4b-c) ----------

def set_missing_since(url, when):
    conn = store.connect()
    conn.execute("UPDATE postings SET missing_since = ? WHERE url_key = ?", (when.isoformat(), store.url_key(url)))
    conn.commit()


def posting(url):
    return dict(store.connect().execute("SELECT * FROM postings WHERE url_key = ?", (store.url_key(url),)).fetchone())


def test_scans_minutes_apart_never_close_anything(db):
    gone = "https://x.com/gone"
    scan("x.com", [gone, "https://x.com/keep"])
    scan("x.com", ["https://x.com/keep"])
    scan("x.com", ["https://x.com/keep"])
    scan("x.com", ["https://x.com/keep"])
    p = posting(gone)
    assert p["missing_since"] and p["closed_at"] is None


def test_a_second_miss_a_day_after_the_first_closes_it(db):
    gone = "https://x.com/gone"
    scan("x.com", [gone, "https://x.com/keep"])
    scan("x.com", ["https://x.com/keep"])
    set_missing_since(gone, datetime.now(timezone.utc) - timedelta(hours=25))
    _, outcome = scan("x.com", ["https://x.com/keep"])
    assert outcome["closed"] == 1 and posting(gone)["closed_at"]


def test_a_second_miss_23_hours_after_the_first_does_not(db):
    gone = "https://x.com/gone"
    scan("x.com", [gone, "https://x.com/keep"])
    scan("x.com", ["https://x.com/keep"])
    set_missing_since(gone, datetime.now(timezone.utc) - timedelta(hours=23))
    _, outcome = scan("x.com", ["https://x.com/keep"])
    assert outcome["closed"] == 0 and posting(gone)["closed_at"] is None


def test_the_gap_is_longer_than_any_cache_lifetime():
    from knowitall import fetch
    assert store.CLOSE_AFTER >= timedelta(hours=24) > fetch.CACHE_TTL      # two observations can never be one cached page


def test_a_posting_that_was_missing_and_returns_is_cleared(db):
    gone = "https://x.com/gone"
    scan("x.com", [gone, "https://x.com/keep"])
    scan("x.com", ["https://x.com/keep"])
    scan("x.com", [gone, "https://x.com/keep"])
    assert posting(gone)["missing_since"] is None


def test_matches_per_run_for_history(db):
    session = store.start_session()
    run, _ = scan("x.com", ["https://x.com/1", "https://x.com/2"], session)
    conn = store.connect()
    conn.execute("UPDATE postings SET workplace = 'remote' WHERE url_key = 'x.com/1'"); conn.commit()
    assert store.run_match_counts({"workplace": ["remote"]}, [run]) == {run: 1}
    assert store.run_match_counts({}, [run]) == {run: 2} and store.run_match_counts({}, []) == {}
