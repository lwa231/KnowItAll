"""SQLite history: every run, every job, and the hash map behind the "new" marker.

A posting counts as new when its normalised URL has never been seen in any previous run,
of any company - that is the whole point of the `seen` table.
"""
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .normalize import url_key

DB_PATH = Path("history.db")

_local = threading.local()
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    domain       TEXT NOT NULL,
    company      TEXT,
    source       TEXT,
    status       TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    jobs_count   INTEGER DEFAULT 0,
    new_count    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL,
    company    TEXT,
    title      TEXT,
    url        TEXT,
    url_key    TEXT,
    location   TEXT,
    remote     INTEGER,
    department TEXT,
    posted     TEXT,
    source     TEXT,
    is_new     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS seen (
    url_key    TEXT PRIMARY KEY,
    domain     TEXT,
    first_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_domain ON runs(domain);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect():
    """One connection per thread; SQLite objects are not shareable across threads."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init():
    conn = connect()
    with _write_lock:
        conn.executescript(SCHEMA)
        conn.commit()


def start_run(domain, company):
    conn = connect()
    with _write_lock:
        cur = conn.execute(
            "INSERT INTO runs (domain, company, status, started_at) VALUES (?, ?, 'running', ?)",
            (domain, company, now_iso()),
        )
        conn.commit()
    return cur.lastrowid


def mark_new(jobs, domain):
    """Tag each job with is_new, then record its hash. Returns the number of new postings."""
    conn = connect()
    keys = [url_key(job.get("url") or "") for job in jobs]
    with _write_lock:
        known = {row["url_key"] for row in conn.execute(
            "SELECT url_key FROM seen WHERE url_key IN (%s)" % ",".join("?" * len(keys)), keys
        )} if keys else set()
        fresh = []
        for job, key in zip(jobs, keys):
            job["is_new"] = bool(key) and key not in known
            if job["is_new"]:
                known.add(key)                     # a duplicate inside one batch is only new once
                fresh.append((key, domain, now_iso()))
        if fresh:
            conn.executemany("INSERT OR IGNORE INTO seen (url_key, domain, first_seen) VALUES (?, ?, ?)", fresh)
            conn.commit()
    return sum(1 for job in jobs if job.get("is_new"))


def save_jobs(run_id, jobs):
    if not jobs:
        return
    conn = connect()
    rows = [(
        run_id, job.get("company"), job.get("title"), job.get("url"), url_key(job.get("url") or ""),
        job.get("location"), None if job.get("remote") is None else int(job["remote"]),
        job.get("department"), job.get("posted"), job.get("source"), int(bool(job.get("is_new"))),
    ) for job in jobs]
    with _write_lock:
        conn.executemany(
            "INSERT INTO jobs (run_id, company, title, url, url_key, location, remote, department, posted,"
            " source, is_new) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()


def finish_run(run_id, status, source, jobs_count, new_count):
    conn = connect()
    with _write_lock:
        conn.execute(
            "UPDATE runs SET status=?, source=?, jobs_count=?, new_count=?, finished_at=? WHERE id=?",
            (status, source, jobs_count, new_count, now_iso(), run_id),
        )
        conn.commit()


def history(limit=200):
    conn = connect()
    return [dict(row) for row in conn.execute(
        "SELECT id, domain, company, source, status, started_at, finished_at, jobs_count, new_count"
        " FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def run_jobs(run_id):
    conn = connect()
    rows = conn.execute(
        "SELECT company, title, url, location, remote, department, posted, source, is_new"
        " FROM jobs WHERE run_id=? ORDER BY id", (run_id,))
    out = []
    for row in rows:
        job = dict(row)
        job["remote"] = None if job["remote"] is None else bool(job["remote"])
        job["is_new"] = bool(job["is_new"])
        out.append(job)
    return out


def previous_run(domain, before_run_id):
    conn = connect()
    row = conn.execute(
        "SELECT id, finished_at FROM runs WHERE domain=? AND id<? AND status!='running'"
        " ORDER BY id DESC LIMIT 1", (domain, before_run_id)).fetchone()
    return dict(row) if row else None


def delete_run(run_id):
    conn = connect()
    with _write_lock:
        conn.execute("DELETE FROM jobs WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
        conn.commit()
