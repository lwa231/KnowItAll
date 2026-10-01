"""Phase 4: "Reuse downloaded pages for: Off / 1 hour / 12 hours" is a real setting with real effects."""
import json

import pytest

from knowitall import fetch, settings
from knowitall.context import RunConfig, ScanContext
from knowitall.runner import Runner, clean_options


def test_the_old_fresh_switch_becomes_off(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"fresh": True}))
    assert settings.load(path)["cache_reuse"] == "off"
    path.write_text(json.dumps({"fresh": False}))
    assert settings.load(path)["cache_reuse"] == "12h"
    path.write_text(json.dumps({"fresh": True, "cache_reuse": "1h"}))
    assert settings.load(path)["cache_reuse"] == "1h"                 # an explicit choice wins


def test_only_the_three_choices_are_accepted():
    assert settings.validate({"cache_reuse": "1h"}) == {"cache_reuse": "1h"}
    for bad in ("2h", "", None, True, "OFF"):
        with pytest.raises(ValueError):
            settings.validate({"cache_reuse": bad})
    assert settings.DEFAULTS["cache_reuse"] == "12h" and "fresh" not in settings.DEFAULTS
    with pytest.raises(ValueError):
        clean_options({"cache_reuse": "forever"})


@pytest.mark.parametrize("choice,cache,reuse", [("off", "REFRESH", "12h"), ("1h", True, "1h"), ("12h", True, "12h")])
def test_the_choice_reaches_the_scan_config(choice, cache, reuse):
    config = Runner()._config_from({"cache_reuse": choice})
    assert (config.cache, config.reuse) == (cache, reuse)


def test_the_cli_no_cache_flag_still_means_refresh():
    assert Runner()._config_from({"fresh": True, "cache_reuse": "12h"}).cache == "REFRESH"


def test_each_lifetime_has_its_own_fetchers_and_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "_fetch_page_1h", lambda url, cache=None, **k: calls.append(("1h", url)) or {"url": url})
    monkeypatch.setattr(fetch, "_fetch_page", lambda url, cache=None, **k: calls.append(("12h", url)) or {"url": url})
    fetch.fetch_page(ScanContext(config=RunConfig(reuse="1h")), "https://a.com/")
    fetch.fetch_page(ScanContext(config=RunConfig(reuse="12h")), "https://b.com/")
    assert calls == [("1h", "https://a.com/"), ("12h", "https://b.com/")]
    assert fetch._fetch_page_1h is not fetch._fetch_page and fetch._fetch_json_1h is not fetch._fetch_json
