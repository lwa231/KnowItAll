"""Phase 3: the filter profile is saved, survives a restart, offers every choice, and its counts are cheap."""
import json

import pytest

from knowitall import store
from knowitall.normalize import make_job
from knowitall.service import Service


def seed(jobs, domain="acme.com"):
    run = store.start_run(domain, "Acme")
    batch = [make_job("Acme", t, f"https://{domain}/jobs/{i}", location=loc, workplace=wp, **geo)
             for i, (t, loc, wp, geo) in enumerate(jobs)]
    store.mark_new(batch, domain)
    store.save_jobs(run, batch)
    store.finish_run(run, "done", "greenhouse", len(batch), len(batch), complete=True)


@pytest.fixture
def filled(app):
    seed([("Remote US", "Remote", "remote", {"country": "US"}), ("Berlin office", "Berlin", "onsite", {"country": "DE"}),
          ("Somewhere", None, None, {}), ("Remote DE", "Remote", "remote", {"country": "DE"})])
    return app


def post(app, path, body):
    status, data, _ = app.call("POST", path, body, app.json)
    return status, json.loads(data)


def test_the_profile_round_trips_and_survives_a_new_service(app):
    status, data = post(app, "/api/filters", {"filters": {"workplace": ["remote"], "country": ["US", "unknown"], "posted_within_days": 7,
                                                           "q": "ignored", "new_only": True, "status": "closed"}})
    assert status == 200 and data["filters"] == {"workplace": ["remote"], "country": ["US", "unknown"], "posted_within_days": 7}
    assert app.service.get_filters() == data["filters"]
    assert json.loads(app.call("GET", "/api/settings", None, app.auth)[1])["filters"] == data["filters"]
    assert Service(settings_path=app.service.settings_path).get_filters() == data["filters"]       # a fresh start


def test_clearing_saves_an_empty_profile_and_other_settings_are_untouched(app):
    app.service.update_settings({"max_jobs": 77})
    post(app, "/api/filters", {"filters": {"workplace": ["hybrid"]}})
    status, data = post(app, "/api/filters", {"filters": {}})
    assert status == 200 and data["filters"] == {} and app.service.get_settings()["max_jobs"] == 77
    app.service.update_settings({"theme": "light"})
    post(app, "/api/filters", {"filters": {"source": ["workday"]}})
    app.service.update_settings({"theme": "dark"})
    assert app.service.get_filters() == {"source": ["workday"]}                # a settings save keeps the profile


@pytest.mark.parametrize("bad", [{"workplace": ["sideways"]}, {"employment_type": ["x"]}, {"region_group": ["Narnia"]},
                                 {"posted_within_days": 0}, {"posted_within_days": "abc"}])
def test_bad_profiles_are_refused_and_nothing_changes(app, bad):
    post(app, "/api/filters", {"filters": {"workplace": ["remote"]}})
    status, data = post(app, "/api/filters", {"filters": bad})
    assert status == 400 and "error" in data
    assert app.service.get_filters() == {"workplace": ["remote"]}
    assert post(app, "/api/filters", {"filters": "x"})[0] == 400


def test_a_damaged_profile_in_the_settings_file_is_ignored(tmp_path):
    from knowitall import settings
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"filters": {"workplace": "remote", "country": [1, None, "US"], "posted_within_days": True, "bogus": 1}}))
    assert settings.load(path)["filters"] == {"country": ["US"]}
    path.write_text(json.dumps({"filters": "nope"}))
    assert settings.load(path)["filters"] == {}


def test_the_country_list_offers_every_country_before_anything_is_scanned(app):
    status, body, _ = app.call("GET", "/api/geo/countries", None, app.auth)
    countries = json.loads(body)["countries"]
    assert status == 200 and len(countries) > 150
    by_code = {c["code"]: c["name"] for c in countries}
    assert by_code["DE"] == "Germany" and by_code["US"] == "United States"
    from knowitall import geo
    assert [c["name"] for c in countries] == sorted((c["name"] for c in countries), key=geo.fold)


def test_the_saved_profile_hides_postings_in_every_query(filled):
    filled.service.set_filters({"workplace": ["remote"]})
    profile = filled.service.get_filters()
    rows = store.query_jobs({**profile})
    assert {j["title"] for j in rows["jobs"]} == {"Remote US", "Remote DE"}
    with_unknown = store.query_jobs({"workplace": ["remote", "unknown"]})
    assert "Somewhere" in {j["title"] for j in with_unknown["jobs"]}


def test_per_company_counts_can_be_asked_for_alone(filled):
    seed([("Other", "Paris", "remote", {"country": "FR"})], domain="other.io")
    full = store.query_jobs({"workplace": ["remote"], "limit": 1})
    cheap = store.query_jobs({"workplace": ["remote"], "limit": 1, "facets": "domain"})
    assert set(cheap["facets"]) == {"domain"} and set(full["facets"]) > {"domain", "workplace"}
    assert {f["value"]: f["count"] for f in cheap["facets"]["domain"]} == {"acme.com": 2, "other.io": 1}
    assert cheap["total"] == 3
    with pytest.raises(ValueError):
        store.query_jobs({"facets": "domain,nonsense"})
    assert "facets" not in store.query_jobs({"facets": "0"})


def test_not_stated_counts_are_what_the_feed_footer_offers(filled):
    data = store.query_jobs({"workplace": ["remote"], "limit": 1})
    unknown = [f for f in data["facets"]["workplace"] if f["value"] is None]
    assert unknown and unknown[0]["count"] == 1                                # the one that does not say


def test_a_region_and_no_location_can_be_combined(filled):
    rows = store.query_jobs({"region_group": ["EMEA"], "country": ["unknown"]})["jobs"]
    assert {j["title"] for j in rows} == {"Berlin office", "Remote DE", "Somewhere"}


def test_regions_and_countries_add_up(filled):
    titles = lambda **f: {j["title"] for j in store.query_jobs(f)["jobs"]}
    assert titles(country=["US"]) == {"Remote US"}
    assert titles(region_group=["EMEA"]) == {"Berlin office", "Remote DE"}
    assert titles(region_group=["EMEA"], country=["US"]) == {"Remote US", "Berlin office", "Remote DE"}
