import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from knowitall import normalize as n, store


def seed():
    def add(domain, company, jobs):
        run_id = store.start_run(domain, company)
        store.save_jobs(run_id, jobs)
        store.finish_run(run_id, "done", "test", len(jobs), 0)
        return run_id

    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    return add("acme.com", "Acme", [
        n.make_job("Acme", "Backend Engineer", "https://acme.com/1", location="Berlin, Germany",
                   workplace="hybrid", employment_type="full_time", department="Engineering", posted=recent),
        n.make_job("Acme", "Designer", "https://acme.com/2", location="Remote - US", department="Design"),
        n.make_job("Acme", "Support Engineer", "https://acme.com/3", location="London, UK",
                   workplace="onsite", employment_type="part_time"),
    ])


def get(app, params):
    query = urlencode(params, doseq=True)
    status, body, _ = app.call("GET", f"/api/jobs?{query}", headers=app.auth)
    return status, json.loads(body)


def test_jobs_endpoint_returns_rows_total_and_facets(app):
    seed()
    status, data = get(app, {})
    assert status == 200
    assert data["total"] == 3 and len(data["jobs"]) == 3
    assert {"jobs", "total", "limit", "offset", "facets"} <= set(data)
    assert {f["value"] for f in data["facets"]["workplace"]} == {"hybrid", "remote", "onsite"}
    first = data["jobs"][0]
    assert {"id", "domain", "workplace", "employment_type", "city", "country", "geo_confidence", "is_new"} <= set(first)


def test_repeated_query_parameters_become_lists(app):
    seed()
    _, data = get(app, {"workplace": ["remote", "hybrid"]})
    assert {j["title"] for j in data["jobs"]} == {"Backend Engineer", "Designer"}
    _, data = get(app, [("country", "DE"), ("country", "GB")])
    assert {j["title"] for j in data["jobs"]} == {"Backend Engineer", "Support Engineer"}


def test_text_search_facets_and_paging_through_http(app):
    seed()
    _, data = get(app, {"q": "engineer -support"})
    assert [j["title"] for j in data["jobs"]] == ["Backend Engineer"]
    _, data = get(app, {"q": "germany", "posted_within_days": 7})
    assert [j["title"] for j in data["jobs"]] == ["Backend Engineer"]
    _, data = get(app, {"limit": 2, "sort": "title", "order": "asc", "facets": 0})
    assert data["total"] == 3 and len(data["jobs"]) == 2 and "facets" not in data
    _, page2 = get(app, {"limit": 2, "offset": 2, "sort": "title", "order": "asc"})
    assert [j["title"] for j in page2["jobs"]] == ["Support Engineer"]


def test_bad_filters_are_a_400_with_a_message(app):
    seed()
    for params in ({"workplace": "sometimes"}, {"limit": "many"}, {"sort": "id; DROP TABLE jobs"},
                   {"region_group": "Narnia"}):
        status, data = get(app, params)
        assert status == 400 and data["error"], params
    assert get(app, {})[0] == 200                                           # and the data is intact


def test_hostile_search_text_is_safe_over_http(app):
    seed()
    for q in ('"', "((", "'; DROP TABLE jobs; --", "NEAR(a b)", "%", "\\"):
        status, _ = get(app, {"q": q})
        assert status == 200, q
    assert get(app, {})[1]["total"] == 3


def test_jobs_endpoint_needs_the_token(app):
    seed()
    assert app.call("GET", "/api/jobs")[0] == 403
    assert app.call("GET", "/api/jobs", headers={"X-KnowItAll-Token": "nope"})[0] == 403


def test_export_all_includes_the_new_columns(app, tmp_path, monkeypatch):
    from knowitall import paths
    run_id = seed()
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")
    runner = app.service.runner
    runner.order = ["acme.com"]
    runner.companies = {"acme.com": {"run_id": run_id, "notes": []}}
    session = app.service.begin_session()                      # "Export this session" exports this launch's scans
    store.connect().execute("UPDATE runs SET session_id = ?", (session,)); store.connect().commit()
    status, body, _ = app.call("POST", "/api/export", {}, app.json)
    written = json.loads(body)
    assert status == 200 and written["rows"] == 3
    header = open(written["written"], encoding="utf-8-sig").read().splitlines()[0]
    assert written["written"].split("/")[-1].startswith("knowitall_session_")
    assert header.split(",")[-6:] == ["workplace", "employment_type", "city", "region", "country", "geo_confidence"]


def test_state_endpoint_matches_the_service(app):
    status, body, _ = app.call("GET", "/api/state?since=0", headers=app.auth)
    state = json.loads(body)
    assert status == 200 and set(state) >= {"running", "companies", "events", "cursor", "notices"}
