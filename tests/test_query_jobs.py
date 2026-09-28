from datetime import datetime, timedelta, timezone

import pytest

from knowitall import normalize as n, store


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture(params=["fts", "like"])
def db(request, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    store.init()
    if request.param == "like":                      # run every text query on the fallback path as well
        monkeypatch.setattr(store, "fts_enabled", lambda conn=None: False)
    yield store
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


def job(title, location, company="Acme", **fields):
    is_new = fields.pop("is_new", False)
    made = n.make_job(company, title, f"https://{company.lower()}.com/{title.replace(' ', '-')}",
                      location=location, **fields)
    made["is_new"] = is_new
    return made


def add_run(db, domain, jobs, status="done", company="Acme"):
    run_id = db.start_run(domain, company)
    db.save_jobs(run_id, jobs)
    db.finish_run(run_id, status, "test", len(jobs), 0)
    return run_id


# The dataset (the newest usable run of each company is what queries see):
#
#   Acme    Senior Backend Engineer   New York, NY      hybrid  full_time  Engineering  2d   greenhouse  new
#           Backend Engineer Intern   Berlin, Germany   onsite  intern     Engineering  40d  greenhouse
#           Product Designer          Remote - EMEA     remote  full_time  Design       -    greenhouse   (no country)
#           Staff ML Engineer         San Francisco, CA remote  full_time  Engineering  5d   greenhouse  new
#   Globex  Data Analyst              London, UK        hybrid  part_time  Data         1d   lever
#           Account Executive         Sunnyvale, CA     onsite  contract   Sales        10d  lever
#           Support Specialist        Remote - US       remote* -          Support      3d   lever       new  (*inferred from text)
#           Office Manager            Toronto, ON       -       -          Operations   6d   lever        (workplace unknown)
@pytest.fixture
def seeded(db):
    old = add_run(db, "acme.com", [job("Old Role", "Austin, TX")])                   # superseded below
    add_run(db, "acme.com", [
        job("Senior Backend Engineer", "New York, NY", workplace="hybrid", employment_type="full_time",
            department="Engineering", posted=days_ago(2), source="greenhouse", is_new=True),
        job("Backend Engineer Intern", "Berlin, Germany", workplace="onsite", employment_type="intern",
            department="Engineering", posted=days_ago(40), source="greenhouse"),
        job("Product Designer", "Remote - EMEA", workplace="remote", employment_type="full_time",
            department="Design", source="greenhouse"),
        job("Staff ML Engineer", "San Francisco, CA", workplace="remote", employment_type="full_time",
            department="Engineering", posted=days_ago(5), source="greenhouse", is_new=True),
    ])
    add_run(db, "globex.com", [
        job("Data Analyst", "London, UK", company="Globex", workplace="hybrid", employment_type="part_time",
            department="Data", posted=days_ago(1), source="lever"),
        job("Account Executive", "Sunnyvale, CA", company="Globex", workplace="onsite", employment_type="contract",
            department="Sales", posted=days_ago(10), source="lever"),
        job("Support Specialist", "Remote - US", company="Globex", department="Support",
            posted=days_ago(3), source="lever", is_new=True),
        job("Office Manager", "Toronto, ON", company="Globex", department="Operations",
            posted=days_ago(6), source="lever"),
    ], company="Globex")
    add_run(db, "globex.com", [job("Ghost Job", "Paris, TX", company="Globex")], status="failed", company="Globex")
    return db, old


def titles(result):
    return [j["title"] for j in result["jobs"]]


def title_set(db, **filters):
    return set(titles(db.query_jobs({"limit": 500, **filters})))


def test_scope_is_the_newest_usable_run_per_company(seeded):
    db, old = seeded
    result = db.query_jobs({})
    assert result["total"] == 8
    assert "Old Role" not in titles(result)            # superseded by acme's newer run
    assert "Ghost Job" not in titles(result)           # globex's newest run failed: its good run still counts
    assert titles(db.query_jobs({"run_id": old})) == ["Old Role"]
    assert {j["domain"] for j in result["jobs"]} == {"acme.com", "globex.com"}


def test_rows_carry_all_fields(seeded):
    db, _ = seeded
    row = db.query_jobs({"q": "Senior Backend"})["jobs"][0]
    assert row["workplace"] == "hybrid" and row["remote"] is False and row["is_new"] is True
    assert (row["city"], row["region"], row["country"], row["geo_confidence"]) == ("New York", "New York", "US", "parsed")
    assert row["employment_type"] == "full_time"
    assert row["id"] and row["run_id"] and row["domain"] == "acme.com"


def test_workplace_and_employment_filters(seeded):
    db, _ = seeded
    assert title_set(db, workplace="remote") == {"Staff ML Engineer", "Product Designer", "Support Specialist"}
    assert db.query_jobs({"workplace": ["remote", "hybrid"]})["total"] == 5
    assert title_set(db, workplace="unknown") == {"Office Manager"}
    assert title_set(db, workplace=["onsite", "unknown"]) == {"Backend Engineer Intern", "Account Executive", "Office Manager"}
    assert title_set(db, employment_type=["intern", "contract"]) == {"Backend Engineer Intern", "Account Executive"}
    assert title_set(db, employment_type="unknown") == {"Support Specialist", "Office Manager"}


def test_source_domain_department_and_new_filters(seeded):
    db, _ = seeded
    assert db.query_jobs({"source": ["lever"]})["total"] == 4
    assert db.query_jobs({"domain": ["globex.com"]})["total"] == 4
    assert db.query_jobs({"department": ["engineering"]})["total"] == 3     # case-insensitive
    assert db.query_jobs({"department": ["Design", "Sales"]})["total"] == 2
    assert title_set(db, new_only="true") == {"Senior Backend Engineer", "Staff ML Engineer", "Support Specialist"}
    assert db.query_jobs({"new_only": "0"})["total"] == 8


def test_country_filter_accepts_names_and_codes_and_unknown(seeded):
    db, _ = seeded
    us = {"Senior Backend Engineer", "Staff ML Engineer", "Account Executive", "Support Specialist"}
    assert title_set(db, country=["US"]) == us
    assert title_set(db, country=["United States"]) == us
    assert title_set(db, country=["Germany"]) == {"Backend Engineer Intern"}
    assert title_set(db, country=["us", "ca"]) == us | {"Office Manager"}
    assert title_set(db, country=["unknown"]) == {"Product Designer"}          # "Remote - EMEA": no country


def test_region_group_matches_countries_and_text_only_locations(seeded):
    db, _ = seeded
    assert title_set(db, region_group="EMEA") == {"Backend Engineer Intern", "Data Analyst", "Product Designer"}
    assert title_set(db, region_group="NORAM") == {
        "Senior Backend Engineer", "Staff ML Engineer", "Account Executive", "Support Specialist", "Office Manager"}
    assert db.query_jobs({"region_group": ["emea", "noram"]})["total"] == 8
    assert title_set(db, region_group="DACH") == {"Backend Engineer Intern"}


def test_region_group_text_match_respects_word_boundaries(db):
    add_run(db, "x.com", [
        job("A", "Remote - EU"), job("B", "Hanoi, Vietnam"), job("C", "Main Menu Street"),
        job("D", "Remote (NAM)"),
    ])
    assert title_set(db, region_group="EU") == {"A"}                      # 'EU' is not inside 'Menu'
    assert title_set(db, region_group="NORAM") == {"D"}                   # 'NAM' is not inside 'Vietnam'


def test_posted_within_days(seeded):
    db, _ = seeded
    assert title_set(db, posted_within_days=7) == {
        "Senior Backend Engineer", "Staff ML Engineer", "Data Analyst", "Support Specialist", "Office Manager"}
    assert db.query_jobs({"posted_within_days": 365})["total"] == 7        # undated Product Designer is excluded


def test_text_search(seeded):
    db, _ = seeded
    engineers = {"Senior Backend Engineer", "Backend Engineer Intern", "Staff ML Engineer"}
    assert title_set(db, q="engineer") == engineers
    assert title_set(db, q="eng") == engineers                             # prefix
    assert title_set(db, q="ENGINEER") == engineers                        # case-insensitive
    assert title_set(db, q="engineer -intern -staff") == {"Senior Backend Engineer"}
    assert title_set(db, q="backend NOT intern") == {"Senior Backend Engineer"}
    assert title_set(db, q="analyst OR designer") == {"Data Analyst", "Product Designer"}
    assert title_set(db, q='"staff ml"') == {"Staff ML Engineer"}
    assert title_set(db, q='"ml staff"') == set()                          # phrases are ordered
    assert db.query_jobs({"q": "-intern"})["total"] == 7                   # negatives alone work
    assert title_set(db, q="globex") == {"Data Analyst", "Account Executive", "Support Specialist", "Office Manager"}


def test_text_search_expands_places(seeded):
    db, _ = seeded
    assert title_set(db, q="nyc") == {"Senior Backend Engineer"}                        # NYC -> New York
    assert title_set(db, q="new york") == {"Senior Backend Engineer"}
    assert title_set(db, q="germany") == {"Backend Engineer Intern"}                    # via country = DE
    assert title_set(db, q="bay area") == {"Staff ML Engineer", "Account Executive"}    # metro expansion
    assert title_set(db, q="EMEA") == {"Backend Engineer Intern", "Data Analyst", "Product Designer"}
    assert title_set(db, q="toronto") == {"Office Manager"}
    assert title_set(db, q="paris") == set()                                            # nothing invented
    assert title_set(db, q='"bay area"') == set()                                       # quoted = exact text only


def test_text_and_facets_combine(seeded):
    db, _ = seeded
    result = db.query_jobs({"q": "engineer", "workplace": ["remote"], "country": ["US"]})
    assert titles(result) == ["Staff ML Engineer"]
    assert db.query_jobs({"q": "engineer", "workplace": ["remote"], "country": ["DE"]})["total"] == 0


def test_default_sort_is_newest_first_with_undated_last(seeded):
    db, _ = seeded
    ordered = db.query_jobs({"limit": 500})["jobs"]
    dated = [j["posted"] for j in ordered if j["posted"]]
    assert dated == sorted(dated, reverse=True)
    assert ordered[0]["title"] == "Data Analyst" and ordered[-1]["title"] == "Product Designer"
    oldest_first = db.query_jobs({"order": "asc", "limit": 500})["jobs"]
    assert oldest_first[0]["title"] == "Backend Engineer Intern" and oldest_first[-1]["posted"] is None


def test_other_sorts_and_pagination(seeded):
    db, _ = seeded
    assert titles(db.query_jobs({"sort": "title", "order": "asc"}))[:2] == ["Account Executive", "Backend Engineer Intern"]
    assert titles(db.query_jobs({"sort": "title", "order": "desc"}))[0] == "Support Specialist"
    page1 = db.query_jobs({"sort": "title", "order": "asc", "limit": 3})
    page2 = db.query_jobs({"sort": "title", "order": "asc", "limit": 3, "offset": 3})
    page3 = db.query_jobs({"sort": "title", "order": "asc", "limit": 3, "offset": 6})
    assert page1["total"] == page2["total"] == page3["total"] == 8
    assert [len(p["jobs"]) for p in (page1, page2, page3)] == [3, 3, 2]
    everything = titles(page1) + titles(page2) + titles(page3)
    assert len(set(everything)) == 8 and everything == sorted(everything, key=str.lower)
    assert (page2["limit"], page2["offset"]) == (3, 3)


def test_facet_counts_ignore_their_own_filter_only(seeded):
    db, _ = seeded
    facets = db.query_jobs({"workplace": ["remote"]})["facets"]
    workplace = {f["value"]: f["count"] for f in facets["workplace"]}
    assert workplace == {"remote": 3, "hybrid": 2, "onsite": 2, None: 1}      # own filter is skipped
    assert facets["workplace"][-1]["value"] is None                           # unknown always last
    country = {f["value"]: f["count"] for f in facets["country"]}
    assert country == {"US": 2, None: 1}                                      # other facets see only remote jobs
    domains = {f["value"]: (f["label"], f["count"]) for f in facets["domain"]}
    assert domains == {"acme.com": ("Acme", 2), "globex.com": ("Globex", 1)}
    assert {"value": "EMEA", "label": "Europe, Middle East & Africa"} in facets["region_group"]
    departments = {f["value"]: f["count"] for f in facets["department"]}
    assert departments == {"Engineering": 1, "Design": 1, "Support": 1}


def test_facet_counts_follow_the_text_search(seeded):
    db, _ = seeded
    facets = db.query_jobs({"q": "engineer"})["facets"]
    assert {f["value"]: f["count"] for f in facets["workplace"]} == {"hybrid": 1, "onsite": 1, "remote": 1}
    assert {f["value"]: f["count"] for f in facets["employment_type"]} == {"full_time": 2, "intern": 1}


def test_facets_can_be_skipped(seeded):
    db, _ = seeded
    assert "facets" not in db.query_jobs({"facets": "0"})
    assert "facets" in db.query_jobs({})


@pytest.mark.parametrize("bad", [
    {"workplace": ["sometimes"]}, {"employment_type": ["forever"]}, {"region_group": ["Narnia"]},
    {"sort": "id; DROP TABLE jobs"}, {"order": "sideways"}, {"limit": "0"}, {"limit": "9999"},
    {"limit": "abc"}, {"offset": "-1"}, {"posted_within_days": "0"}, {"posted_within_days": "x"}, {"run_id": "nope"},
])
def test_bad_input_raises_value_error(seeded, bad):
    db, _ = seeded
    with pytest.raises(ValueError):
        db.query_jobs(bad)


@pytest.mark.parametrize("q", ['"', '""', "((", "AND", "* : ^", 'a"b', "NEAR(a b)", "'; DROP TABLE jobs; --",
                               "%", "_", "\\", "a OR", "OR a", "-", "- -", "NOT NOT", "\x00", "𝒳 emoji 😀"])
def test_hostile_search_text_never_errors_or_hurts(seeded, q):
    db, _ = seeded
    db.query_jobs({"q": q})
    assert db.query_jobs({})["total"] == 8                                  # nothing was dropped


def test_empty_database_and_unmatched_filters(db):
    result = db.query_jobs({})
    assert result["jobs"] == [] and result["total"] == 0
    assert all(v == [] for k, v in result["facets"].items() if k != "region_group")
