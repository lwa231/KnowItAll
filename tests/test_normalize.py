from datetime import datetime, timezone

from knowitall import normalize as n


def test_to_iso_epoch_ms_and_iso_strings():
    assert n.to_iso(1740000000000).startswith("2025-02-19T")
    assert n.to_iso("2025-03-01T10:00:00-05:00") == "2025-03-01T15:00:00+00:00"
    assert n.to_iso("2025-03-01") == "2025-03-01T00:00:00+00:00"
    assert n.to_iso("2025-03-01T10:00:00Z") == "2025-03-01T10:00:00+00:00"


def test_to_iso_rejects_junk():
    for junk in (None, "", "   ", "not a date"):
        assert n.to_iso(junk) is None


def test_to_iso_workday_relative_text_is_day_precision():
    today = n.to_iso("Posted Today")
    parsed = datetime.fromisoformat(today)
    assert (parsed.hour, parsed.minute) == (0, 0)
    assert parsed.date() == datetime.now(timezone.utc).date()
    three = datetime.fromisoformat(n.to_iso("Posted 3 Days Ago"))
    assert (parsed - three).days == 3
    assert n.to_iso("Posted 30+ Days Ago") is not None


def test_clean_joins_lists_and_collapses_whitespace():
    assert n.clean("  a \n b  ") == "a b"
    assert n.clean(["NYC", "NYC", None, "SF"]) == "NYC | SF"
    assert n.clean({"name": "Eng"}) == "Eng"
    assert n.clean("   ") is None and n.clean(None) is None


def test_workplace_to_remote_collapses_hybrid_today():
    # Characterisation: H5 will add a real 'hybrid' value; until then hybrid reads as False.
    assert n.workplace_to_remote("Remote") is True
    assert n.workplace_to_remote("Hybrid") is False
    assert n.workplace_to_remote("On-site") is False
    assert n.workplace_to_remote("Unspecified") is None
    assert n.workplace_to_remote(None) is None


def test_make_job_infers_remote_but_never_guesses_false():
    job = n.make_job("Acme", "Engineer", "https://x.com/1", location="Remote - US")
    assert job["remote"] is True
    job = n.make_job("Acme", "Engineer", "https://x.com/2", location="Berlin")
    assert job["remote"] is None
    job = n.make_job("Acme", "Engineer", "https://x.com/3", location="Remote", remote=False)
    assert job["remote"] is False           # an explicit feed value wins over the text guess


def test_make_job_has_schema_fields():
    job = n.make_job("Acme", "Engineer", "https://x.com/1", source="lever")
    assert set(n.FIELDS) <= set(job)


def test_url_key_normalises_scheme_www_slash_and_fragment():
    a = n.url_key("https://www.Example.com/jobs/1/#apply")
    b = n.url_key("http://example.com/jobs/1")
    assert a == b == "example.com/jobs/1"


def test_url_key_keeps_only_gh_jid():
    assert n.url_key("https://x.com/careers?gh_jid=42&utm_source=a") == "x.com/careers?gh_jid=42"
    assert n.url_key("https://x.com/careers?utm_source=a") == "x.com/careers"
    assert n.url_key("") == "" and n.url_key(None) == ""


def test_dedupe_drops_duplicates_and_incomplete_rows():
    jobs = [
        {"title": "A", "url": "https://x.com/1"},
        {"title": "A again", "url": "http://www.x.com/1/"},
        {"title": None, "url": "https://x.com/2"},
        {"title": "No url", "url": None},
        {"title": "B", "url": "https://x.com/3"},
    ]
    assert [j["title"] for j in n.dedupe(jobs)] == ["A", "B"]
