import pytest

from knowitall import store


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


def job(url, title="Engineer", **extra):
    return {"company": "Acme", "title": title, "url": url, "location": "Berlin", "remote": None,
            "department": None, "posted": None, "source": "lever", **extra}


def scan(db, domain, jobs, status="done", source="lever", complete=True):
    """One company scan, the way the Runner does it: mark new, save, finish."""
    run_id = db.start_run(domain, domain)
    batch = [dict(j) for j in jobs]
    new = db.mark_new(batch, domain)
    db.save_jobs(run_id, batch)
    outcome = db.finish_run(run_id, status, source, len(batch), new, complete=complete)
    return run_id, outcome


def posting(db, url):
    row = db.connect().execute("SELECT * FROM postings WHERE url_key = ?", (store.url_key(url),)).fetchone()
    return dict(row) if row else None


# ---------- new / known ----------

def test_mark_new_only_reads_so_a_url_is_new_until_it_has_been_saved(db):
    batch = [job("https://x.com/1"), job("https://x.com/2")]
    assert db.mark_new(batch, "x.com") == 2
    assert db.mark_new([job("https://x.com/1")], "x.com") == 1          # nothing was recorded by the first call
    db.save_jobs(db.start_run("x.com", "X"), batch)
    again = [job("https://www.x.com/1/"), job("https://x.com/3")]       # first one is the same posting (same url_key)
    assert db.mark_new(again, "x.com") == 1
    assert [j["is_new"] for j in again] == [False, True]


def test_mark_new_counts_a_duplicate_inside_a_batch_once(db):
    assert db.mark_new([job("https://x.com/1"), job("https://x.com/1")], "x.com") == 1


def test_a_url_known_from_another_company_is_not_new(db):
    scan(db, "a.com", [job("https://shared.com/jobs/1")])
    assert db.mark_new([job("https://shared.com/jobs/1")], "b.com") == 0


def test_a_reopened_posting_is_not_new(db):
    url = "https://x.com/1"
    scan(db, "x.com", [job(url), job("https://x.com/2")])
    scan(db, "x.com", [job("https://x.com/2")])
    scan(db, "x.com", [job("https://x.com/2")])                          # url is now closed
    assert posting(db, url)["closed_at"]
    assert db.mark_new([job(url)], "x.com") == 0


# ---------- postings ----------

def test_run_lifecycle_and_round_trip(db):
    run_id = db.start_run("x.com", "Acme")
    batch = [job("https://x.com/1", remote=True), job("https://x.com/2")]
    db.mark_new(batch, "x.com")
    db.save_jobs(run_id, batch)
    db.finish_run(run_id, "done", "lever", 2, 2)

    rows = db.run_jobs(run_id)
    assert [r["title"] for r in rows] == ["Engineer", "Engineer"]
    assert rows[0]["remote"] is True and rows[1]["remote"] is None
    assert all(r["is_new"] is True for r in rows)
    runs = db.history()
    assert runs[0]["id"] == run_id and runs[0]["status"] == "done"
    assert runs[0]["jobs_count"] == 2 and runs[0]["new_count"] == 2


def test_history_is_newest_first(db):
    a = db.start_run("a.com", "A")
    b = db.start_run("b.com", "B")
    assert [r["id"] for r in db.history()] == [b, a]


def test_saving_the_same_url_again_updates_one_posting_instead_of_copying_it(db):
    first_run, _ = scan(db, "x.com", [job("https://x.com/1", title="Old title", location="Berlin")])
    before = posting(db, "https://x.com/1")
    second_run, _ = scan(db, "x.com", [job("https://x.com/1", title="New title", location="Munich, Germany")])
    after = posting(db, "https://x.com/1")
    count = db.connect().execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    assert count == 1
    assert after["id"] == before["id"]
    assert after["title"] == "New title" and after["location"].startswith("Munich")
    assert after["first_seen"] == before["first_seen"]                    # when it first appeared never moves
    assert after["last_seen"] >= before["last_seen"] and after["last_run_id"] == second_run
    assert [r["title"] for r in db.run_jobs(first_run)] == ["New title"]  # old runs show the latest fields
    assert db.run_jobs(second_run)[0]["is_new"] is False


def test_each_run_links_the_postings_it_saw(db):
    r1, _ = scan(db, "x.com", [job("https://x.com/1"), job("https://x.com/2")])
    r2, _ = scan(db, "x.com", [job("https://x.com/2"), job("https://x.com/3")])
    assert {j["url"] for j in db.run_jobs(r1)} == {"https://x.com/1", "https://x.com/2"}
    assert {j["url"] for j in db.run_jobs(r2)} == {"https://x.com/2", "https://x.com/3"}
    assert {j["url"]: j["is_new"] for j in db.run_jobs(r2)} == {"https://x.com/2": False, "https://x.com/3": True}


def test_rows_without_a_url_are_skipped_and_duplicates_in_a_run_link_once(db):
    run_id = db.start_run("x.com", "X")
    db.save_jobs(run_id, [job(None), job("https://x.com/1"), job("https://www.x.com/1/")])
    assert db.connect().execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 1
    assert db.connect().execute("SELECT COUNT(*) FROM run_postings").fetchone()[0] == 1
    db.save_jobs(run_id, [])                                              # empty batch: fine


def test_the_search_index_follows_changes_but_ignores_untouched_rows(db):
    scan(db, "x.com", [job("https://x.com/1", title="Quantum Plumber")])
    assert db.query_jobs({"q": "quantum"})["total"] == 1
    scan(db, "x.com", [job("https://x.com/1", title="Classical Electrician")])
    assert db.query_jobs({"q": "quantum"})["total"] == 0
    assert db.query_jobs({"q": "electrician"})["total"] == 1
    conn = db.connect()
    fts_rows = conn.execute("SELECT COUNT(*) FROM postings_fts WHERE postings_fts MATCH 'electrician'").fetchone()[0]
    assert fts_rows == 1                                                  # replaced, not duplicated


# ---------- removal: missing, then closed ----------

def test_two_consecutive_complete_scans_close_a_posting_and_a_return_reopens_it(db):
    gone, keep = "https://x.com/gone", "https://x.com/keep"
    _, first = scan(db, "x.com", [job(gone), job(keep)])
    assert first == {"missing": 0, "closed": 0}
    _, second = scan(db, "x.com", [job(keep)])
    assert second == {"missing": 1, "closed": 0}
    p = posting(db, gone)
    assert p["missing_since"] and not p["closed_at"]
    _, third = scan(db, "x.com", [job(keep)])
    assert third == {"missing": 0, "closed": 1}
    p = posting(db, gone)
    assert p["closed_at"] and p["missing_since"]
    scan(db, "x.com", [job(gone), job(keep)])                              # it is back
    p = posting(db, gone)
    assert p["closed_at"] is None and p["missing_since"] is None
    assert posting(db, keep)["missing_since"] is None                      # a posting that never left is untouched


def test_a_posting_that_returns_after_one_miss_never_closes(db):
    url = "https://x.com/1"
    scan(db, "x.com", [job(url), job("https://x.com/2")])
    scan(db, "x.com", [job("https://x.com/2")])                            # missing
    scan(db, "x.com", [job(url), job("https://x.com/2")])                  # back before the second miss
    scan(db, "x.com", [job("https://x.com/2")])                            # missing again: a fresh first miss
    p = posting(db, url)
    assert p["missing_since"] and p["closed_at"] is None


@pytest.mark.parametrize("status, complete", [
    ("stopped", True), ("failed", True), ("running", True), ("done", False),
])
def test_incomplete_scans_never_mark_anything(db, status, complete):
    scan(db, "x.com", [job("https://x.com/1"), job("https://x.com/2")])
    _, outcome = scan(db, "x.com", [job("https://x.com/2")], status=status, complete=complete)
    assert outcome == {"missing": 0, "closed": 0}
    assert posting(db, "https://x.com/1")["missing_since"] is None


def test_an_incomplete_scan_between_two_complete_ones_does_not_count(db):
    gone = "https://x.com/gone"
    scan(db, "x.com", [job(gone), job("https://x.com/keep")])
    scan(db, "x.com", [job("https://x.com/keep")])                         # miss 1 (complete)
    scan(db, "x.com", [job("https://x.com/keep")], complete=False)         # truncated: ignored
    assert posting(db, gone)["closed_at"] is None
    scan(db, "x.com", [job("https://x.com/keep")])                         # miss 2 (complete)
    assert posting(db, gone)["closed_at"]


def test_a_scan_that_found_nothing_closes_nothing(db):
    scan(db, "x.com", [job("https://x.com/1"), job("https://x.com/2")])
    for _ in range(3):
        _, outcome = scan(db, "x.com", [])                                 # site glitch, not "everything was removed"
        assert outcome == {"missing": 0, "closed": 0}
    assert posting(db, "https://x.com/1")["missing_since"] is None


def test_a_change_of_hiring_platform_closes_nothing(db):
    scan(db, "x.com", [job("https://x.com/1"), job("https://x.com/2")], source="greenhouse")
    _, outcome = scan(db, "x.com", [job("https://other.io/9")], source="lever")
    assert outcome == {"missing": 0, "closed": 0, "skipped": "source changed"}
    assert posting(db, "https://x.com/1")["missing_since"] is None


def test_finding_the_board_by_name_is_the_same_platform(db):
    scan(db, "x.com", [job("https://x.com/1"), job("https://x.com/2")], source="greenhouse")
    _, outcome = scan(db, "x.com", [job("https://x.com/2")], source="greenhouse (by name)")
    assert outcome == {"missing": 1, "closed": 0}


def test_removal_is_per_company(db):
    scan(db, "a.com", [job("https://a.com/1"), job("https://a.com/2")])
    scan(db, "b.com", [job("https://b.com/1")])
    scan(db, "a.com", [job("https://a.com/2")])
    assert posting(db, "https://a.com/1")["missing_since"]
    assert posting(db, "https://b.com/1")["missing_since"] is None


def test_status_views_show_what_went_missing_and_closed(db):
    scan(db, "x.com", [job("https://x.com/a", title="Stays"), job("https://x.com/b", title="Leaves")])
    scan(db, "x.com", [job("https://x.com/a", title="Stays")])
    missing = db.query_jobs({"status": "missing"})
    assert [j["title"] for j in missing["jobs"]] == ["Leaves"] and missing["jobs"][0]["status"] == "missing"
    assert missing["jobs"][0]["missing_since"] and missing["jobs"][0]["closed_at"] is None
    assert [j["title"] for j in db.query_jobs({"status": "current"})["jobs"]] == ["Stays"]
    assert db.query_jobs({"status": "closed"})["total"] == 0
    scan(db, "x.com", [job("https://x.com/a", title="Stays")])
    closed = db.query_jobs({"status": "closed"})
    assert [j["title"] for j in closed["jobs"]] == ["Leaves"] and closed["jobs"][0]["status"] == "closed"
    assert db.query_jobs({"status": "missing"})["total"] == 0
    assert db.query_jobs({"status": "closed", "q": "leaves"})["total"] == 1     # the filters work on this view too
    assert db.query_jobs({"status": "closed", "q": "stays"})["total"] == 0
    with pytest.raises(ValueError):
        db.query_jobs({"status": "deleted"})


def test_current_rows_carry_their_timeline(db):
    scan(db, "x.com", [job("https://x.com/1")])
    row = db.query_jobs({})["jobs"][0]
    assert row["status"] == "open" and row["first_seen"] and row["last_seen"]
    assert row["missing_since"] is None and row["closed_at"] is None
