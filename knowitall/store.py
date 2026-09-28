"""SQLite history: every run, and one row per posting with when it was first and last seen.

A *posting* is one job listing, identified by its normalised URL (`url_key`). It is created the first time
any scan sees it and updated in place every later time, so the database can answer questions a pile of
per-run copies could not: when did this first appear, is it still listed, what has disappeared.

  runs          one row per scan of one company
  postings      one row per job listing: latest fields, first_seen / last_seen, missing_since / closed_at
  run_postings  which postings a run saw (and whether each was new in that run): a link, not a copy

A posting counts as new when its normalised URL is not yet in `postings`, for any company.

Removal is inferred carefully. After a *complete* scan, a posting that was not listed gets `missing_since`;
if the next complete scan still does not list it, `closed_at` is set; if it ever comes back both are cleared.
A scan is not complete (and never closes anything) when it was stopped or failed, was cut short by a size
cap, found nothing at all, or read the company from a different hiring platform than the scan before.

The schema is versioned (table `schema_version`) and upgraded by ordered migrations in MIGRATIONS; each runs
in one transaction and rolls back completely if it fails. Version 1 is the original schema (databases that
predate the version table are recognised as v1), 2 adds the structured job fields and search index, 3
replaces the per-run job copies with `postings` (a backup of the database is written first).
"""
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import geo, paths, search
from .normalize import EMPLOYMENT_TYPES, WORKPLACES, url_key

DB_PATH = paths.DB_PATH

_local = threading.local()
_write_lock = threading.Lock()

# ---------- schema and migrations ----------

V1_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        domain       TEXT NOT NULL,
        company      TEXT,
        source       TEXT,
        status       TEXT,
        started_at   TEXT,
        finished_at  TEXT,
        jobs_count   INTEGER DEFAULT 0,
        new_count    INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS jobs (
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
    )""",
    """CREATE TABLE IF NOT EXISTS seen (
        url_key    TEXT PRIMARY KEY,
        domain     TEXT,
        first_seen TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_domain ON runs(domain)",
]

STRUCTURED_COLUMNS = [("workplace", "TEXT"), ("employment_type", "TEXT"), ("city", "TEXT"),
                      ("region", "TEXT"), ("country", "TEXT"), ("geo_confidence", "TEXT")]
BACKFILL_BATCH = 1000


def _migrate_v1(conn):
    for statement in V1_STATEMENTS:
        conn.execute(statement)


def _fts_sql(table, columns):
    """The FTS5 table over `table` and the triggers that keep it in step with inserts, updates, deletes."""
    new = ", ".join(f"new.{c.strip()}" for c in columns.split(","))
    old = ", ".join(f"old.{c.strip()}" for c in columns.split(","))
    fts = f"{table}_fts"
    return [
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5(
            {columns}, content='{table}', content_rowid='id', tokenize='unicode61 remove_diacritics 2')""",
        f"""CREATE TRIGGER IF NOT EXISTS {fts}_insert AFTER INSERT ON {table} BEGIN
            INSERT INTO {fts}(rowid, {columns}) VALUES (new.id, {new}); END""",
        f"""CREATE TRIGGER IF NOT EXISTS {fts}_delete AFTER DELETE ON {table} BEGIN
            INSERT INTO {fts}({fts}, rowid, {columns}) VALUES ('delete', old.id, {old}); END""",
        f"""CREATE TRIGGER IF NOT EXISTS {fts}_update AFTER UPDATE OF {columns} ON {table} BEGIN
            INSERT INTO {fts}({fts}, rowid, {columns}) VALUES ('delete', old.id, {old});
            INSERT INTO {fts}(rowid, {columns}) VALUES (new.id, {new}); END""",
    ]


def _add_fts(conn, table, columns):
    """Create the FTS index if this SQLite has FTS5; without it, search falls back to LIKE."""
    statements = _fts_sql(table, columns)
    try:
        conn.execute(statements[0])
    except sqlite3.OperationalError:
        return False
    for statement in statements[1:]:
        conn.execute(statement)
    conn.execute(f"INSERT INTO {table}_fts({table}_fts) VALUES ('rebuild')")
    return True


def _migrate_v2(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, kind in STRUCTURED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
    # Old rows: what we can know for certain. Remote was stored as 1 only when it really was remote
    # (0 could have been hybrid, so it is left unknown), and the location text can be read as it is now.
    conn.execute("UPDATE jobs SET workplace = 'remote' WHERE workplace IS NULL AND remote = 1")
    rows = conn.execute("SELECT id, location FROM jobs WHERE location IS NOT NULL AND geo_confidence IS NULL").fetchall()
    for start in range(0, len(rows), BACKFILL_BATCH):
        updates = []
        for row_id, location in rows[start:start + BACKFILL_BATCH]:
            parsed = geo.parse_location(location)
            if parsed:
                updates.append((parsed["city"], parsed["region"], parsed["country"], "parsed", row_id))
        conn.executemany("UPDATE jobs SET city=?, region=?, country=?, geo_confidence=? WHERE id=?", updates)


POSTING_COLUMNS = ("company", "title", "url", "location", "remote", "department", "posted", "source", "workplace",
                   "employment_type", "city", "region", "country", "geo_confidence")
FTS_COLUMNS = "title, company, location, department, city, region, country"


def _migrate_v3(conn):
    conn.execute("""CREATE TABLE postings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        url_key         TEXT NOT NULL UNIQUE,
        domain          TEXT,
        company         TEXT,
        title           TEXT,
        url             TEXT,
        location        TEXT,
        remote          INTEGER,
        department      TEXT,
        posted          TEXT,
        source          TEXT,
        workplace       TEXT,
        employment_type TEXT,
        city            TEXT,
        region          TEXT,
        country         TEXT,
        geo_confidence  TEXT,
        first_seen      TEXT,
        last_seen       TEXT,
        missing_since   TEXT,
        closed_at       TEXT,
        last_run_id     INTEGER
    )""")
    conn.execute("""CREATE TABLE run_postings (
        run_id     INTEGER NOT NULL,
        posting_id INTEGER NOT NULL,
        is_new     INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (run_id, posting_id)
    ) WITHOUT ROWID""")
    conn.execute("CREATE INDEX idx_run_postings_posting ON run_postings(posting_id)")
    for name, column in (("domain", "domain"), ("posted", "posted"), ("country", "country"), ("workplace", "workplace"),
                         ("state", "closed_at, missing_since"), ("last_run", "last_run_id")):
        conn.execute(f"CREATE INDEX idx_postings_{name} ON postings({column})")

    now = now_iso()
    # One posting per URL, holding the fields of the newest run that listed it.
    conn.execute("""
        INSERT INTO postings (url_key, domain, company, title, url, location, remote, department, posted, source,
                              workplace, employment_type, city, region, country, geo_confidence,
                              first_seen, last_seen, last_run_id)
        SELECT j.url_key, r.domain, j.company, j.title, j.url, j.location, j.remote, j.department, j.posted, j.source,
               j.workplace, j.employment_type, j.city, j.region, j.country, j.geo_confidence,
               COALESCE(s.first_seen, (SELECT MIN(r0.started_at) FROM jobs j0 JOIN runs r0 ON r0.id = j0.run_id
                                       WHERE j0.url_key = j.url_key), ?),
               COALESCE(r.finished_at, r.started_at, ?), j.run_id
        FROM jobs j
        JOIN (SELECT MAX(id) AS id FROM jobs WHERE url_key IS NOT NULL AND url_key != '' GROUP BY url_key) latest
             ON latest.id = j.id
        JOIN runs r ON r.id = j.run_id
        LEFT JOIN seen s ON s.url_key = j.url_key
    """, (now, now))
    conn.execute("""
        INSERT OR IGNORE INTO run_postings (run_id, posting_id, is_new)
        SELECT j.run_id, p.id, MAX(COALESCE(j.is_new, 0))
        FROM jobs j JOIN postings p ON p.url_key = j.url_key
        GROUP BY j.run_id, p.id
    """)
    _add_fts(conn, "postings", FTS_COLUMNS)
    for statement in ("DROP TRIGGER IF EXISTS jobs_fts_insert", "DROP TRIGGER IF EXISTS jobs_fts_delete",
                      "DROP TABLE IF EXISTS jobs_fts", "DROP TABLE jobs", "DROP TABLE seen"):
        conn.execute(statement)


MIGRATIONS = [(1, _migrate_v1), (2, _migrate_v2), (3, _migrate_v3)]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _has_word(text, word):
    """SQL helper: does `word` appear in `text` as a whole word/phrase ('EU' in 'Remote - EU', not in 'Menu')?"""
    return int(bool(text) and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I) is not None)


def connect():
    """One connection per thread; SQLite objects are not shareable across threads."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.create_function("has_word", 2, _has_word, deterministic=True)
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def schema_version(conn=None):
    conn = conn or connect()
    if not _table_exists(conn, "schema_version"):
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def _backup(conn, from_version):
    """A consistent copy of the database before a migration that rewrites its tables (kept, never overwritten)."""
    if not _table_exists(conn, "runs") or conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0:
        return None                                       # nothing worth keeping
    path = Path(DB_PATH)
    target = path.with_name(f"{path.name}.v{from_version}.bak")
    if target.exists():
        return target
    destination = sqlite3.connect(str(target))
    try:
        conn.backup(destination)
    finally:
        destination.close()
    return target


def init():
    """Create or upgrade the database. Safe to call on every start."""
    conn = connect()
    with _write_lock:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        conn.commit()
        current = schema_version(conn)
        if current == 0 and _table_exists(conn, "runs"):
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")     # a database from before versioning
            conn.commit()
            current = 1
        for version, migrate in MIGRATIONS:
            if version <= current:
                continue
            if version == 3:
                _backup(conn, current)
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate(conn)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise


def fts_enabled(conn=None):
    return _table_exists(conn or connect(), "postings_fts")


# ---------- runs and postings ----------

def start_run(domain, company):
    conn = connect()
    with _write_lock:
        cur = conn.execute(
            "INSERT INTO runs (domain, company, status, started_at) VALUES (?, ?, 'running', ?)",
            (domain, company, now_iso()),
        )
        conn.commit()
    return cur.lastrowid


def mark_new(jobs, domain=None):
    """Tag each job with is_new (its URL is not yet a known posting). Returns how many are new.

    Only reads: the posting itself is recorded by save_jobs, so the two calls belong together."""
    conn = connect()
    keys = [url_key(job.get("url") or "") for job in jobs]
    known = {row[0] for row in conn.execute(
        "SELECT url_key FROM postings WHERE url_key IN (%s)" % ",".join("?" * len(keys)), keys
    )} if keys else set()
    counted = set()
    for job, key in zip(jobs, keys):
        job["is_new"] = bool(key) and key not in known and key not in counted
        counted.add(key)                            # a duplicate inside one batch is only new once
    return sum(1 for job in jobs if job["is_new"])


def _job_values(job):
    remote = job.get("remote")
    values = {name: job.get(name) for name in POSTING_COLUMNS}
    values["remote"] = None if remote is None else int(remote)
    return values


def save_jobs(run_id, jobs):
    """Record what a run saw: create postings for new URLs, refresh the ones already known (they are
    listed again, so any 'missing'/'closed' mark is cleared), and link them all to the run."""
    if not jobs:
        return
    conn = connect()
    now = now_iso()
    with _write_lock:
        run = conn.execute("SELECT domain FROM runs WHERE id = ?", (run_id,)).fetchone()
        domain = run["domain"] if run else None
        links = []
        for job in jobs:
            key = url_key(job.get("url") or "")
            if not key:
                continue
            values = _job_values(job)
            existing = conn.execute(f"SELECT id, {', '.join(POSTING_COLUMNS)} FROM postings WHERE url_key = ?",
                                    (key,)).fetchone()
            if existing is None:
                cursor = conn.execute(
                    f"INSERT INTO postings (url_key, domain, {', '.join(POSTING_COLUMNS)}, first_seen, last_seen,"
                    f" last_run_id) VALUES (?, ?, {', '.join('?' * len(POSTING_COLUMNS))}, ?, ?, ?)",
                    (key, domain, *values.values(), now, now, run_id))
                posting_id = cursor.lastrowid
            else:
                posting_id = existing["id"]
                changed = {k: v for k, v in values.items() if existing[k] != v}
                if changed:                               # touch the searchable columns only when they changed
                    conn.execute(f"UPDATE postings SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?",
                                 (*changed.values(), posting_id))
                conn.execute("UPDATE postings SET domain = ?, last_seen = ?, last_run_id = ?, missing_since = NULL,"
                             " closed_at = NULL WHERE id = ?", (domain, now, run_id, posting_id))
            links.append((run_id, posting_id, int(bool(job.get("is_new")))))
        conn.executemany("INSERT OR IGNORE INTO run_postings (run_id, posting_id, is_new) VALUES (?, ?, ?)", links)
        conn.commit()


def _base_source(source):
    return re.sub(r"\s*\(by name\)$", "", source or "")


def _close_missing(conn, run_id, domain, source, now):
    """Two consecutive complete scans missing -> closed; the first miss only marks missing_since."""
    previous = conn.execute(
        "SELECT source FROM runs WHERE domain = ? AND id < ? AND status = 'done' ORDER BY id DESC LIMIT 1",
        (domain, run_id)).fetchone()
    if previous and _base_source(previous["source"]) != _base_source(source):
        return {"missing": 0, "closed": 0, "skipped": "source changed"}
    closed = conn.execute(
        "UPDATE postings SET closed_at = ? WHERE domain = ? AND closed_at IS NULL AND missing_since IS NOT NULL"
        " AND COALESCE(last_run_id, 0) != ?", (now, domain, run_id)).rowcount
    missing = conn.execute(
        "UPDATE postings SET missing_since = ? WHERE domain = ? AND closed_at IS NULL AND missing_since IS NULL"
        " AND COALESCE(last_run_id, 0) != ?", (now, domain, run_id)).rowcount
    return {"missing": missing, "closed": closed}


def finish_run(run_id, status, source, jobs_count, new_count, complete=False):
    """Close out a run. `complete` says the scan read the company's whole listing (not stopped, not cut
    short by a size cap); only then, and only if it found something, may postings be marked missing/closed.
    Returns {"missing": n, "closed": n} for what this run changed."""
    conn = connect()
    now = now_iso()
    outcome = {"missing": 0, "closed": 0}
    with _write_lock:
        conn.execute(
            "UPDATE runs SET status=?, source=?, jobs_count=?, new_count=?, finished_at=? WHERE id=?",
            (status, source, jobs_count, new_count, now, run_id),
        )
        row = conn.execute("SELECT domain FROM runs WHERE id = ?", (run_id,)).fetchone()
        if complete and status == "done" and jobs_count and row:
            outcome = _close_missing(conn, run_id, row["domain"], source, now)
        conn.commit()
    return outcome


def history(limit=200):
    conn = connect()
    return [dict(row) for row in conn.execute(
        "SELECT id, domain, company, source, status, started_at, finished_at, jobs_count, new_count"
        " FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


JOB_SELECT = ("p.company, p.title, p.url, p.location, p.remote, p.department, p.posted, p.source,"
              " COALESCE(rp.is_new, 0) AS is_new, p.workplace, p.employment_type, p.city, p.region, p.country,"
              " p.geo_confidence")


def _job_dict(row):
    job = dict(row)
    job["remote"] = None if job["remote"] is None else bool(job["remote"])
    job["is_new"] = bool(job["is_new"])
    return job


def run_jobs(run_id):
    """The postings a run saw, with their latest fields (and whether each was new in that run)."""
    conn = connect()
    rows = conn.execute(
        f"SELECT {JOB_SELECT} FROM run_postings rp JOIN postings p ON p.id = rp.posting_id"
        " WHERE rp.run_id = ? ORDER BY p.id", (run_id,))
    return [_job_dict(row) for row in rows]


# ---------- retention ----------

KEEP_RUNS_PER_COMPANY = 50
KEEP_CLOSED_DAYS = 180


def prune(keep_runs=KEEP_RUNS_PER_COMPANY, closed_days=KEEP_CLOSED_DAYS, now=None):
    """Forget what is no longer useful: scans beyond the newest `keep_runs` of each company (with their links),
    and postings that have been closed for more than `closed_days`. Returns what was removed.

    Postings that are still listed are never touched, however old their scans were."""
    conn = connect()
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=closed_days)).isoformat()
    with _write_lock:
        old_runs = "(SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY domain ORDER BY id DESC) AS n" \
                   " FROM runs WHERE status != 'running') WHERE n > ?)"
        conn.execute(f"DELETE FROM run_postings WHERE run_id IN {old_runs}", (keep_runs,))
        runs = conn.execute(f"DELETE FROM runs WHERE id IN {old_runs}", (keep_runs,)).rowcount
        conn.execute("DELETE FROM run_postings WHERE posting_id IN (SELECT id FROM postings WHERE closed_at < ?)", (cutoff,))
        postings = conn.execute("DELETE FROM postings WHERE closed_at IS NOT NULL AND closed_at < ?", (cutoff,)).rowcount
        conn.commit()
    return {"runs": runs, "postings": postings}


def database_size():
    path = Path(DB_PATH)
    return sum(p.stat().st_size for p in (path, path.with_name(path.name + "-wal")) if p.exists())


def compact():
    """Rewrite the database file without the free space deleted rows leave behind. Returns the sizes."""
    conn = connect()
    with _write_lock:
        before = database_size()
        conn.commit()
        if fts_enabled(conn):
            conn.execute("INSERT INTO postings_fts(postings_fts) VALUES ('optimize')")
            conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = database_size()
    return {"before": before, "after": after}


# ---------- querying ----------
# One seam for "give me jobs matching these filters": the API calls only query_jobs(), so the tables
# behind it can change without touching the endpoint or the UI.

MAX_LIMIT = 500
DEFAULT_LIMIT = 100
LIST_FILTERS = ("workplace", "employment_type", "country", "region_group", "department", "source", "domain")
FACET_FIELDS = {                     # facet -> (SQL expression, can the value be unknown/NULL?)
    "workplace": ("p.workplace", True),
    "employment_type": ("p.employment_type", True),
    "country": ("p.country", True),
    "department": ("p.department", True),
    "source": ("p.source", False),
    "domain": ("p.domain", False),
}
SORTS = {"posted": "p.posted", "title": "lower(p.title)", "company": "lower(p.company)",
         "location": "lower(p.location)", "found": "p.id", "first_seen": "p.first_seen", "last_seen": "p.last_seen"}
STATUSES = ("current", "missing", "closed")
LIVE_STATUSES = "('running', 'done', 'stopped')"
UNKNOWN = "unknown"
FACET_LIMIT = 100


def _as_list(value):
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(v).strip() for v in values if str(v).strip()]


def _as_int(name, value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number") from None
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return number


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_filters(raw):
    """Validate/clean a filters dict (lists or single values, as parse_qs gives them). Raises ValueError."""
    raw = raw or {}

    def first(key):
        value = raw.get(key)
        return value[0] if isinstance(value, (list, tuple)) and value else value

    filters = {key: _as_list(raw.get(key)) for key in LIST_FILTERS}
    for name, allowed in (("workplace", WORKPLACES), ("employment_type", EMPLOYMENT_TYPES)):
        filters[name] = [v.lower() for v in filters[name]]
        bad = [v for v in filters[name] if v not in allowed and v != UNKNOWN]
        if bad:
            raise ValueError(f"unknown {name}: {', '.join(bad)} (use {', '.join(allowed)} or {UNKNOWN})")
    countries = []
    for value in filters["country"]:
        countries.append(UNKNOWN if value.lower() == UNKNOWN else (geo.normalize_country(value) or value.upper()))
    filters["country"] = countries
    for group in filters["region_group"]:
        if geo.find_region_group(group) is None:
            raise ValueError(f"unknown region group: {group}")
    filters["region_group"] = [geo.find_region_group(g) for g in filters["region_group"]]

    filters["q"] = str(first("q") or "").strip()[:500]
    days = first("posted_within_days")
    filters["posted_within_days"] = _as_int("posted_within_days", days, 1, 3650) if days not in (None, "") else None
    filters["new_only"] = _as_bool(first("new_only")) if first("new_only") is not None else False
    run_id = first("run_id")
    filters["run_id"] = _as_int("run_id", run_id, 1, 2**62) if run_id not in (None, "") else None
    status = str(first("status") or "current").lower()
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    filters["status"] = status
    sort = str(first("sort") or "posted").lower()
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    filters["sort"] = sort
    order = str(first("order") or "desc").lower()
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    filters["order"] = order
    filters["limit"] = _as_int("limit", first("limit") or DEFAULT_LIMIT, 1, MAX_LIMIT)
    filters["offset"] = _as_int("offset", first("offset") or 0, 0, 10**9)
    filters["facets"] = _as_bool(first("facets")) if first("facets") is not None else True
    return filters


def _in_clause(expression, values, nullable):
    """expression IN (...), where the literal 'unknown' also matches NULL."""
    wanted = [v for v in values if v != UNKNOWN]
    parts, params = [], []
    if wanted:
        parts.append(f"{expression} IN ({','.join('?' * len(wanted))})")
        params += wanted
    if UNKNOWN in values and nullable:
        parts.append(f"{expression} IS NULL")
    return ("(" + " OR ".join(parts) + ")" if parts else "0"), params


def _scope(filters):
    """(FROM clause, params for it, WHERE fragment) for which postings are being looked at:
    what the newest scan of each company saw ('current'), one given run, or what has gone missing / closed."""
    if filters["run_id"]:
        return ("postings p JOIN run_postings rp ON rp.posting_id = p.id AND rp.run_id = ?",
                [filters["run_id"]], "")
    if filters["status"] == "current":
        return ("postings p JOIN run_postings rp ON rp.posting_id = p.id", [],
                f"rp.run_id IN (SELECT MAX(id) FROM runs WHERE status IN {LIVE_STATUSES} GROUP BY domain)")
    state = ("p.missing_since IS NOT NULL AND p.closed_at IS NULL" if filters["status"] == "missing"
             else "p.closed_at IS NOT NULL")
    return ("postings p LEFT JOIN run_postings rp ON rp.posting_id = p.id AND rp.run_id = p.last_run_id", [], state)


def _where(filters, conn, skip=None):
    """(FROM, WHERE, params) for a normalised filters dict. `skip` leaves one facet's own filter out (for its counts)."""
    from_sql, params, scope_where = _scope(filters)
    clauses = [scope_where] if scope_where else []

    for name, (expression, nullable) in FACET_FIELDS.items():
        if name == skip or not filters.get(name):
            continue
        values = [v.lower() for v in filters[name]] if name == "department" else filters[name]
        expr = f"lower({expression})" if name == "department" else expression
        sql, more = _in_clause(expr, values, nullable)
        clauses.append(sql)
        params += more

    if filters["region_group"] and skip != "country":
        parts = []
        for group in filters["region_group"]:
            countries = sorted(geo.region_group_countries(group))
            if countries:
                parts.append(f"p.country IN ({','.join('?' * len(countries))})")
                params += countries
            for alias in geo.region_group_aliases(group):          # "Remote - EMEA" has no country to match
                parts.append("has_word(p.location, ?)")
                params.append(alias)
        clauses.append("(" + " OR ".join(parts) + ")")

    if filters["posted_within_days"]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=filters["posted_within_days"])).replace(microsecond=0)
        clauses.append("p.posted >= ?")
        params.append(cutoff.isoformat())
    if filters["new_only"]:
        clauses.append("rp.is_new = 1")

    if filters["q"]:
        if fts_enabled(conn):
            positive, negative = search.to_fts(filters["q"])
            if positive:
                clauses.append("p.id IN (SELECT rowid FROM postings_fts WHERE postings_fts MATCH ?)")
                params.append(positive)
            if negative:
                clauses.append("p.id NOT IN (SELECT rowid FROM postings_fts WHERE postings_fts MATCH ?)")
                params.append(negative)
        else:
            sql, more = search.to_like(filters["q"])
            if sql:
                clauses.append(f"({sql})")
                params += more
    return from_sql, (" AND ".join(clauses) or "1"), params


def _facet(conn, filters, name):
    expression, nullable = FACET_FIELDS[name]
    from_sql, where, params = _where(filters, conn, skip=name)
    label = ", MAX(p.company) AS label" if name == "domain" else ""
    rows = conn.execute(
        f"SELECT {expression} AS value, COUNT(*) AS count{label} FROM {from_sql} WHERE {where}"
        f" GROUP BY {expression} ORDER BY count DESC, value LIMIT {FACET_LIMIT}", params).fetchall()
    items = []
    for row in rows:
        if row["value"] is None:
            if nullable:
                items.append({"value": None, "count": row["count"]})       # jobs where this is not known
            continue
        item = {"value": row["value"], "count": row["count"]}
        if name == "domain":
            item["label"] = row["label"]
        items.append(item)
    items.sort(key=lambda item: item["value"] is None)                     # "unknown" always last
    return items


QUERY_SELECT = ("p.id AS id, COALESCE(rp.run_id, p.last_run_id) AS run_id, p.domain AS domain, " + JOB_SELECT +
                ", p.first_seen, p.last_seen, p.missing_since, p.closed_at")


def _posting_dict(row):
    job = _job_dict(row)
    job["status"] = "closed" if job["closed_at"] else "missing" if job["missing_since"] else "open"
    return job


def query_jobs(raw_filters=None):
    """Postings matching the filters, one page of them, with counts for building filter controls.

    Scope: what the newest scan of each company saw (status=current, the default), one specific run_id, or
    the postings that have gone missing / closed. Returns {"jobs": [...], "total": n, "limit": l, "offset": o,
    "facets": {...}}; raises ValueError for bad input.
    """
    filters = normalize_filters(raw_filters)
    conn = connect()
    from_sql, where, params = _where(filters, conn)
    direction = "ASC" if filters["order"] == "asc" else "DESC"
    expression = SORTS[filters["sort"]]
    nulls_last = f"{expression} IS NULL, " if filters["sort"] in ("posted", "location", "company", "title") else ""
    order = f"{nulls_last}{expression} {direction}, p.id DESC"
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM {from_sql} WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {QUERY_SELECT} FROM {from_sql} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [filters["limit"], filters["offset"]]).fetchall()
        facets = {name: _facet(conn, filters, name) for name in FACET_FIELDS} if filters["facets"] else None
    except sqlite3.OperationalError as error:
        if "fts5" in str(error).lower() or "malformed match" in str(error).lower():
            raise ValueError("could not understand that search") from None
        raise
    result = {"jobs": [_posting_dict(row) for row in rows],
              "total": total, "limit": filters["limit"], "offset": filters["offset"]}
    if facets is not None:
        facets["region_group"] = [{"value": key, "label": label} for key, label in geo.region_groups()]
        result["facets"] = facets
    return result
