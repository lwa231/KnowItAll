import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from bs4 import BeautifulSoup

from knowitall import fetch, maintenance, paths, store
from knowitall.service import Service


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
    return {"company": "Acme", "title": title, "url": url, "location": "Berlin, Germany", "source": "lever"}


def scan(db, domain, jobs, status="done", complete=True):
    run_id = db.start_run(domain, domain)
    batch = [dict(j) for j in jobs]
    db.mark_new(batch, domain)
    db.save_jobs(run_id, batch)
    db.finish_run(run_id, status, "lever", len(batch), 0, complete=complete)
    return run_id


# ---------- page cache ----------

def make_cache(root, ages_in_days):
    now = time.time()
    for index, days in enumerate(ages_in_days):
        folder = root / f"_fetch_page{index % 2}"
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / f"{index}.json"
        file.write_text("x" * 100)
        stamp = now - days * 86400
        os.utime(file, (stamp, stamp))


def test_prune_cache_removes_only_old_files_and_empty_folders(tmp_path):
    cache = tmp_path / "cache"
    make_cache(cache, [1, 3, 8, 20])                        # folder0: 1d, 8d   folder1: 3d, 20d
    removed = maintenance.prune_cache(cache, max_age_days=7)
    assert removed == {"files": 2, "bytes": 200}
    assert sorted(p.name for p in cache.rglob("*.json")) == ["0.json", "1.json"]
    make_cache(tmp_path / "cache2", [30, 40])
    maintenance.prune_cache(tmp_path / "cache2", max_age_days=7)
    assert not list((tmp_path / "cache2").iterdir())        # emptied sub-folders are removed too


def test_prune_cache_with_no_folder_is_fine(tmp_path):
    assert maintenance.prune_cache(tmp_path / "nope") == {"files": 0, "bytes": 0}
    assert maintenance.clear_cache(tmp_path / "nope") == {"files": 0, "bytes": 0}


def test_clear_cache_removes_everything_and_the_parsed_page_cache(tmp_path):
    cache = tmp_path / "cache"
    make_cache(cache, [0, 1, 2])
    fetch.soup_of({"html": "<html><body>hi</body></html>"})
    assert fetch._SOUPS.stats()["pages"] >= 1
    assert maintenance.clear_cache(cache) == {"files": 3, "bytes": 300}
    assert cache.exists() and not list(cache.rglob("*.json"))
    assert fetch._SOUPS.stats() == {"pages": 0, "html_bytes": 0}


def test_folder_size_and_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")
    make_cache(tmp_path / "cache", [0, 0])
    assert maintenance.folder_size(tmp_path / "cache") == 200
    assert maintenance.folder_size(tmp_path / "missing") == 0
    usage = maintenance.usage()
    assert usage["cache_bytes"] == 200 and usage["logs_bytes"] == 0 and usage["exports_bytes"] == 0
    assert {"database_bytes", "parsed_pages"} <= set(usage)


# ---------- history ----------

def test_prune_keeps_the_newest_scans_of_each_company(db):
    a_runs = [scan(db, "a.com", [job(f"https://a.com/{n}")]) for n in range(5)]
    b_runs = [scan(db, "b.com", [job(f"https://b.com/{n}")]) for n in range(2)]
    removed = db.prune(keep_runs=3)
    assert removed["runs"] == 2
    left = {r["id"] for r in db.history()}
    assert left == set(a_runs[2:]) | set(b_runs)            # a.com's two oldest are gone; b.com untouched
    links = {r[0] for r in db.connect().execute("SELECT DISTINCT run_id FROM run_postings")}
    assert links == left                                    # their links went with them
    assert db.prune(keep_runs=3) == {"runs": 0, "postings": 0}       # idempotent


def test_a_scan_in_progress_is_never_pruned(db):
    running = db.start_run("a.com", "A")                    # status 'running'
    for n in range(4):
        scan(db, "a.com", [job(f"https://a.com/{n}")])
    db.prune(keep_runs=1)
    assert running in {r["id"] for r in db.history()}


def test_long_closed_postings_are_forgotten_but_listed_ones_never_are(db):
    keep, gone, recent = "https://x.com/keep", "https://x.com/gone", "https://x.com/recent"
    scan(db, "x.com", [job(keep), job(gone), job(recent)])
    scan(db, "x.com", [job(keep)])
    scan(db, "x.com", [job(keep)])                           # gone and recent are now closed
    conn = db.connect()
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    conn.execute("UPDATE postings SET closed_at = ? WHERE url_key = ?", (old, "x.com/gone"))
    conn.execute("UPDATE postings SET first_seen = ?, last_seen = ? WHERE url_key = ?", (old, old, keep))   # very old, still listed
    conn.commit()
    removed = db.prune(keep_runs=50, closed_days=180)
    assert removed["postings"] == 1
    urls = {r[0] for r in conn.execute("SELECT url_key FROM postings")}
    assert urls == {"x.com/keep", "x.com/recent"}
    assert db.query_jobs({"status": "closed"})["total"] == 1          # 'recent' closed 0 days ago: kept
    assert db.query_jobs({"status": "current"})["total"] == 1         # 'keep'


def test_pruning_keeps_the_search_index_consistent(db):
    scan(db, "x.com", [job("https://x.com/1", title="Quantum Plumber"), job("https://x.com/2", title="Stays")])
    scan(db, "x.com", [job("https://x.com/2", title="Stays")])
    scan(db, "x.com", [job("https://x.com/2", title="Stays")])
    db.connect().execute("UPDATE postings SET closed_at = '2000-01-01T00:00:00+00:00' WHERE url_key = 'x.com/1'")
    db.connect().commit()
    db.prune(closed_days=180)
    hits = db.connect().execute("SELECT COUNT(*) FROM postings_fts WHERE postings_fts MATCH 'quantum'").fetchone()[0]
    assert hits == 0
    assert db.query_jobs({"q": "stays"})["total"] == 1
    check = db.connect().execute("INSERT INTO postings_fts(postings_fts) VALUES ('integrity-check')")   # raises if inconsistent
    assert check is not None


# ---------- compact ----------

def test_compact_shrinks_the_file_after_deletions(db):
    for n in range(6):
        scan(db, f"c{n}.com", [job(f"https://c{n}.com/{i}", title="x" * 200) for i in range(150)])
    for n in range(1, 6):
        conn = db.connect()
        conn.execute("DELETE FROM run_postings WHERE run_id IN (SELECT id FROM runs WHERE domain = ?)", (f"c{n}.com",))
        conn.execute("DELETE FROM postings WHERE domain = ?", (f"c{n}.com",))
        conn.commit()
    result = db.compact()
    assert result["after"] < result["before"]
    assert db.query_jobs({})["total"] == 150                # nothing else was harmed
    assert db.query_jobs({"q": "xxxx"})["total"] == 150     # and the index still lines up with the rows


# ---------- parsed-page cache ----------

def test_soup_cache_hits_return_the_same_tree_and_key_on_a_digest():
    cache = fetch._SoupCache(budget_bytes=10_000)
    html = "<html><body><a href='/x'>x</a></body></html>"
    first, second = cache.get(html), cache.get(html)
    assert first is second and cache.stats() == {"pages": 1, "html_bytes": len(html)}
    assert all(isinstance(key, bytes) and len(key) == 16 for key in cache._items)   # the page text itself is not a key


def test_soup_cache_evicts_oldest_first_past_the_byte_budget():
    cache = fetch._SoupCache(budget_bytes=300)
    pages = [f"<html><body>{'p' * 90}{n}</body></html>" for n in range(6)]
    trees = [cache.get(p) for p in pages]
    stats = cache.stats()
    assert stats["html_bytes"] <= 300 and stats["pages"] < 6
    assert cache.get(pages[-1]) is trees[-1]                # newest still cached
    assert cache.get(pages[0]) is not trees[0]              # oldest was evicted, so it is parsed again


def test_soup_cache_does_not_keep_a_page_bigger_than_the_whole_budget():
    cache = fetch._SoupCache(budget_bytes=100)
    huge = "<html><body>" + "x" * 500 + "</body></html>"
    assert cache.get(huge) is not None and cache.stats() == {"pages": 0, "html_bytes": 0}


def test_soup_cache_is_safe_under_threads():
    cache = fetch._SoupCache(budget_bytes=2000)
    pages = [f"<html><body>{'q' * 100}{n}</body></html>" for n in range(30)]
    errors = []

    def work():
        try:
            for _ in range(20):
                for page in pages:
                    cache.get(page)
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=work) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    assert not errors and cache.stats()["html_bytes"] <= 2000


REFERENCE_PAGES = [
    "<html><head><title>Jobs</title><style>.a{}</style></head><body><h1>Open   roles</h1><script>var x=1;</script>"
    "<svg><title>icon</title><text>glyph</text></svg><p>Some <b>bold</b>\n text</p><noscript>turn on js</noscript>"
    "<template><p>hidden</p></template></body></html>",
    "<html><body><div id='root'></div></body></html>",
    "",
    "<html><body>" + "<p>word " * 500 + "</body></html>",
]


def reference_visible_length(html):
    """The previous implementation: re-parse, delete the invisible tags, collapse whitespace."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return len(re.sub(r"\s+", " ", soup.get_text(" ")).strip())


@pytest.mark.parametrize("html", REFERENCE_PAGES)
def test_visible_text_length_matches_the_old_algorithm(html):
    assert fetch.visible_text_length({"html": html}) == reference_visible_length(html)


def test_visible_text_length_does_not_damage_the_cached_page():
    page = {"html": REFERENCE_PAGES[0]}
    fetch.visible_text_length(page)
    assert fetch.soup_of(page).find("script") is not None        # the cached tree is untouched
    assert fetch.visible_text_length(page) == fetch.visible_text_length(page)


# ---------- service and API ----------

def test_housekeeping_never_raises_even_when_a_step_breaks(monkeypatch):
    monkeypatch.setattr(store, "prune", lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    summary = maintenance.run_startup_prune()
    assert "error" in summary["history"] and "files" in summary["cache"]


def test_prune_in_background_runs_and_finishes(tmp_path, monkeypatch, db):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    make_cache(tmp_path / "cache", [30])
    thread = Service().prune_in_background()
    thread.join(5)
    assert not thread.is_alive() and not list((tmp_path / "cache").rglob("*.json"))


def test_compact_is_refused_while_a_scan_runs(db):
    service = Service()
    service.runner.running = True
    assert service.compact_database() is None
    service.runner.running = False
    assert set(service.compact_database()) == {"before", "after"}


def test_maintenance_endpoints(app, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    make_cache(tmp_path / "cache", [0, 0, 0])
    status, body, _ = app.call("GET", "/api/maintenance", headers=app.auth)
    data = json.loads(body)
    assert status == 200 and data["cache_bytes"] == 300 and data["running"] is False
    assert app.call("GET", "/api/maintenance")[0] == 403                          # token required like everything else
    assert app.call("POST", "/api/maintenance/clear-cache", {}, {"Content-Type": "application/json"})[0] == 403
    status, body, _ = app.call("POST", "/api/maintenance/clear-cache", {}, app.json)
    assert status == 200 and json.loads(body) == {"files": 3, "bytes": 300}
    status, body, _ = app.call("POST", "/api/maintenance/compact", {}, app.json)
    assert status == 200 and set(json.loads(body)) == {"before", "after"}
    app.service.runner.running = True
    status, body, _ = app.call("POST", "/api/maintenance/compact", {}, app.json)
    assert status == 409 and "scan is running" in json.loads(body)["error"]
    app.service.runner.running = False
