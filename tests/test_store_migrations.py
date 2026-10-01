import sqlite3
from pathlib import Path

import pytest

from knowitall import store


@pytest.fixture
def path(tmp_path, monkeypatch):
    target = tmp_path / "history.db"
    monkeypatch.setattr(store, "DB_PATH", target)
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    yield target
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


def tables(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    finally:
        conn.close()


def make_v1_database(path):
    """A database exactly as the pre-versioning app left it: two scans of one company, plus a second company."""
    conn = sqlite3.connect(path)
    for statement in store.V1_STATEMENTS:
        conn.execute(statement)
    conn.execute("INSERT INTO runs (id, domain, company, status, source, started_at, finished_at, jobs_count, new_count)"
                 " VALUES (1, 'a.com', 'Acme', 'done', 'lever', '2025-01-01T00:00:00+00:00', '2025-01-01T00:05:00+00:00', 3, 3)")
    conn.execute("INSERT INTO runs (id, domain, company, status, source, started_at, finished_at, jobs_count, new_count)"
                 " VALUES (2, 'a.com', 'Acme', 'done', 'lever', '2025-02-01T00:00:00+00:00', '2025-02-01T00:05:00+00:00', 2, 0)")
    conn.execute("INSERT INTO runs (id, domain, company, status, started_at) VALUES (3, 'b.com', 'Globex', 'done', '2025-02-02T00:00:00+00:00')")
    rows = [
        # run 1: three jobs
        (1, "Acme", "Backend Engineer", "https://a.com/1", "a.com/1", "Austin, TX", 1, "Eng", None, "lever", 1),
        (1, "Acme", "Designer (old title)", "https://a.com/2", "a.com/2", "Berlin, Germany", 0, "Design", None, "lever", 1),
        (1, "Acme", "Analyst", "https://a.com/3", "a.com/3", "Paris", None, None, None, "lever", 1),
        # run 2: job 1 again with a new title, job 2 again, job 3 gone
        (2, "Acme", "Backend Engineer II", "https://a.com/1", "a.com/1", "Austin, TX", 1, "Eng", None, "lever", 0),
        (2, "Acme", "Designer", "https://a.com/2", "a.com/2", "Berlin, Germany", 0, "Design", None, "lever", 0),
        (3, "Globex", "Writer", "https://b.com/1", "b.com/1", None, None, None, None, "lever", 1),
        (3, "Globex", "No link", None, "", None, None, None, None, "lever", 0),
    ]
    conn.executemany("INSERT INTO jobs (run_id, company, title, url, url_key, location, remote, department,"
                     " posted, source, is_new) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO seen (url_key, domain, first_seen) VALUES (?,?,?)",
                     [("a.com/1", "a.com", "2024-12-25T00:00:00+00:00"), ("a.com/2", "a.com", "2025-01-01T00:01:00+00:00")])
    conn.commit()
    conn.close()


def test_fresh_database_reaches_the_latest_version(path):
    store.init()
    assert store.schema_version() == max(v for v, _ in store.MIGRATIONS) == 4
    names = tables(path)
    assert {"runs", "postings", "run_postings", "schema_version", "postings_fts"} <= names
    assert not {"jobs", "seen", "jobs_fts"} & names
    assert store.fts_enabled()
    assert not list(path.parent.glob("*.bak"))                              # nothing to back up


def test_init_is_idempotent(path):
    store.init()
    store.init()
    versions = [r[0] for r in store.connect().execute("SELECT version FROM schema_version ORDER BY version")]
    assert versions == [1, 2, 3, 4]


def test_legacy_v1_database_becomes_postings_without_losing_anything(path):
    make_v1_database(path)
    store.init()
    assert store.schema_version() == 4
    conn = store.connect()

    postings = {r["url_key"]: r for r in conn.execute("SELECT * FROM postings")}
    assert set(postings) == {"a.com/1", "a.com/2", "a.com/3", "b.com/1"}     # one per URL; the linkless row is dropped
    one = postings["a.com/1"]
    assert one["title"] == "Backend Engineer II"                             # the newest run's fields
    assert one["first_seen"] == "2024-12-25T00:00:00+00:00"                  # from the old `seen` table
    assert one["last_run_id"] == 2 and one["domain"] == "a.com"
    assert (one["city"], one["region"], one["country"]) == ("Austin", "Texas", "US")   # v2 backfill carried through
    assert one["workplace"] == "remote"
    assert postings["a.com/2"]["title"] == "Designer" and postings["a.com/2"]["country"] == "DE"
    assert postings["a.com/2"]["workplace"] is None                          # remote=0 might have been hybrid
    assert postings["a.com/3"]["first_seen"] == "2025-01-01T00:00:00+00:00"  # no `seen` row: first run that listed it
    assert postings["a.com/3"]["last_run_id"] == 1
    assert postings["a.com/3"]["missing_since"] is None and postings["a.com/3"]["closed_at"] is None

    links = {(r["run_id"], r["posting_id"]): r["is_new"] for r in conn.execute("SELECT * FROM run_postings")}
    assert len(links) == 6
    assert links[(1, one["id"])] == 1 and links[(2, one["id"])] == 0         # new in run 1, not in run 2

    assert {r["id"] for r in conn.execute("SELECT id FROM runs")} == {1, 2, 3}      # scan history is untouched
    assert not {"jobs", "seen", "jobs_fts"} & tables(path)

    assert [j["title"] for j in store.run_jobs(1)][:2] == ["Backend Engineer II", "Designer"]
    assert store.query_jobs({"q": "austin"})["total"] == 1                   # searchable straight away
    assert {j["title"] for j in store.query_jobs({})["jobs"]} == {"Backend Engineer II", "Designer", "Writer"}


def test_the_migration_backs_up_the_old_database_first(path):
    make_v1_database(path)
    store.init()
    backup = path.with_name(path.name + ".v1.bak")
    assert backup.exists()
    old = sqlite3.connect(backup)
    try:
        assert {"jobs", "seen", "runs"} <= {r[0] for r in old.execute("SELECT name FROM sqlite_master")}
        assert old.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 7        # every row, exactly as it was
        assert "postings" not in {r[0] for r in old.execute("SELECT name FROM sqlite_master")}
    finally:
        old.close()


def test_an_existing_backup_is_never_overwritten(path):
    make_v1_database(path)
    backup = path.with_name(path.name + ".v1.bak")
    backup.write_text("precious")
    store.init()
    assert backup.read_text() == "precious"


def test_v2_database_upgrades_and_backs_up_as_v2(path, monkeypatch):
    make_v1_database(path)
    monkeypatch.setattr(store, "MIGRATIONS", store.MIGRATIONS[:2])
    store.init()
    conn = store.connect()                                                    # ...and the search index H5 built on `jobs`
    for statement in store._fts_sql("jobs", store.FTS_COLUMNS)[:3]:
        conn.execute(statement)
    conn.execute("INSERT INTO jobs_fts(jobs_fts) VALUES ('rebuild')")
    conn.commit()
    assert store.schema_version() == 2 and "jobs_fts" in tables(path)
    monkeypatch.undo()
    monkeypatch.setattr(store, "DB_PATH", path)
    store.init()
    assert store.schema_version() == 4
    assert "jobs_fts" not in tables(path) and "postings_fts" in tables(path)
    assert path.with_name(path.name + ".v2.bak").exists()
    assert store.query_jobs({"q": "austin"})["total"] == 1


def test_failed_v3_migration_rolls_back_completely_but_keeps_the_backup(path, monkeypatch):
    make_v1_database(path)

    def broken(conn):
        store._migrate_v3(conn)
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "MIGRATIONS", store.MIGRATIONS[:2] + [(3, broken)])
    with pytest.raises(RuntimeError):
        store.init()
    assert store.schema_version() == 2                                        # still a valid, usable v2 database
    names = tables(path)
    assert {"jobs", "seen"} <= names and not {"postings", "run_postings"} & names
    assert store.connect().execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 7
    assert path.with_name(path.name + ".v1.bak").exists() or path.with_name(path.name + ".v2.bak").exists()
    monkeypatch.setattr(store, "MIGRATIONS", store.MIGRATIONS[:2] + [(3, store._migrate_v3)])
    store.init()                                                              # and it upgrades fine afterwards
    assert store.schema_version() == 3                                        # (this test's migration list stops at 3)


def test_legacy_v1_rollback_leaves_the_original_tables_intact(path, monkeypatch):
    make_v1_database(path)

    def broken(conn):
        conn.execute("ALTER TABLE jobs ADD COLUMN workplace TEXT")
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "MIGRATIONS", [(1, store._migrate_v1), (2, broken)])
    with pytest.raises(RuntimeError):
        store.init()
    assert store.schema_version() == 1
    columns = {r[1] for r in store.connect().execute("PRAGMA table_info(jobs)")}
    assert "workplace" not in columns                                         # the ALTER was undone


def test_without_fts5_the_migration_still_completes_and_search_falls_back(path, monkeypatch):
    real_connect = store.connect

    class NoFts:
        """Wraps a connection and refuses to create FTS5 tables, like a SQLite built without it."""
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if "CREATE VIRTUAL" in sql and "fts5" in sql.lower():
                raise sqlite3.OperationalError("no such module: fts5")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    make_v1_database(path)
    monkeypatch.setattr(store, "connect", lambda: NoFts(real_connect()))
    store.init()
    assert store.schema_version(real_connect()) == 4
    assert not store.fts_enabled(real_connect())
    monkeypatch.setattr(store, "connect", real_connect)
    assert store.query_jobs({"q": "austin"})["total"] == 1                     # LIKE fallback works on migrated data
