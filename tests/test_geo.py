import json

import pytest

from knowitall import geo


def parsed(text):
    result = geo.parse_location(text)
    return None if result is None else (result["city"], result["region"], result["country"])


@pytest.mark.parametrize("text, expected", [
    # City, state code / name
    ("San Francisco, CA", ("San Francisco", "California", "US")),
    ("Austin, TX", ("Austin", "Texas", "US")),
    ("New York, NY", ("New York", "New York", "US")),
    ("Seattle, Washington", ("Seattle", "Washington", "US")),
    ("San Francisco, California, United States", ("San Francisco", "California", "US")),
    ("Boston, MA, USA", ("Boston", "Massachusetts", "US")),
    ("Toronto, ON", ("Toronto", "Ontario", "CA")),
    ("Vancouver, British Columbia, Canada", ("Vancouver", "British Columbia", "CA")),
    # City, country
    ("London, United Kingdom", ("London", None, "GB")),
    ("London, UK", ("London", None, "GB")),
    ("London, England", ("London", None, "GB")),
    ("Berlin, Germany", ("Berlin", None, "DE")),
    ("Berlin, DE", ("Berlin", None, "DE")),                   # DE is also Delaware; Berlin decides
    ("Paris, France", ("Paris", None, "FR")),
    ("Kraków, Poland", ("Krakow", None, "PL")),               # canonical spelling from the city table
    ("Gliwice, Poland", ("Gliwice", None, "PL")),             # not in the table, still clearly a city
    ("Bengaluru, Karnataka", ("Bangalore", None, "IN")),      # unknown region token; the city is unambiguous
    ("São Paulo, Brazil", ("São Paulo", None, "BR")),
    # Single tokens
    ("London", ("London", None, "GB")),
    ("NYC", ("New York", "New York", "US")),
    ("SF", ("San Francisco", "California", "US")),
    ("Tel Aviv", ("Tel Aviv", None, "IL")),
    ("Germany", (None, None, "DE")),
    ("United States", (None, None, "US")),
    ("USA", (None, None, "US")),
    ("California", (None, "California", "US")),
    ("Singapore", ("Singapore", None, "SG")),
    # Workplace words are stripped, geography kept
    ("Remote - US", (None, None, "US")),
    ("Remote, United States", (None, None, "US")),
    ("Remote (Germany)", (None, None, "DE")),
    ("US - Remote", (None, None, "US")),
    ("Remote in Canada", (None, None, "CA")),
    ("Hybrid - London", ("London", None, "GB")),
    ("San Francisco, CA (Hybrid)", ("San Francisco", "California", "US")),
    # Shapes found in live data (Workday "Country, City"; bullets; commas inside city names)
    ("Israel, Yokneam", ("Yokneam", None, "IL")),
    ("India, Bengaluru", ("Bangalore", None, "IN")),
    ("US, CA, Santa Clara", ("Santa Clara", "California", "US")),
    ("China, Shanghai", ("Shanghai", None, "CN")),
    ("Taiwan, Hsinchu", ("Hsinchu", None, "TW")),
    ("India, Pune (+1 more)", ("Pune", None, "IN")),
    ("Washington, D.C.", ("Washington, D.C.", "District of Columbia", "US")),
    ("Washington, DC", ("Washington, D.C.", "District of Columbia", "US")),
    ("San Francisco, CA • New York, NY • United States", ("San Francisco", "California", "US")),
    ("New York City • Remote", ("New York", "New York", "US")),
    # Multiple places: first resolvable wins
    ("London | Berlin", ("London", None, "GB")),
    ("Multiple Locations | Dublin, Ireland", ("Dublin", None, "IE")),
])
def test_confident_readings(text, expected):
    assert parsed(text) == expected


@pytest.mark.parametrize("text", [
    "Paris",                       # Paris, France or Paris, Texas
    "Dublin",                      # Ireland or Dublin, California
    "Cambridge",                   # UK or Massachusetts
    "Portland",                    # Oregon or Maine
    "Georgia",                     # the country or the US state
    "Tbilisi, Georgia",            # ambiguous name, city unknown to the table
    "Washington",                  # state or DC
    "CA",                          # Canada or California
    "DE",
    "IN",
    "Remote - DE",
    "Wilmington, DE",              # Delaware or Germany, and Wilmington is not in the table
    "Portland, ME",                # Maine or Montenegro; the only Portland known is Oregon
    "Remote",
    "Multiple Locations",
    "Anywhere",
    "Global",
    "Worldwide",
    "TBD",
    "N/A",
    "",
    None,
    "Remote - EMEA",               # a region group, not a country
    "Building 5 Floor 3",
    "Georgia, Tbilisi",            # reversed order, but Georgia is ambiguous
    "CA, Toronto",                 # reversed order with a code that is also California
    "2 Locations",
    "Poland Warsaw (+3 more)",     # space-joined text has no structure to trust; the Workday slug supplies it
])
def test_ambiguous_or_unknown_stays_null(text):
    assert parsed(text) is None


def test_paris_texas_is_texas_not_france():
    assert parsed("Paris, TX") == ("Paris", "Texas", "US")


def test_atlanta_georgia_is_the_us_state():
    assert parsed("Atlanta, Georgia") == ("Atlanta", "Georgia", "US")
    assert parsed("Atlanta, GA") == ("Atlanta", "Georgia", "US")


def test_dublin_california_is_not_ireland():
    result = parsed("Dublin, CA")
    assert result is None or result[2] == "US"                  # never Ireland


def test_city_that_belongs_elsewhere_is_not_forced_into_the_qualifier():
    assert parsed("Cambridge, MA") == ("Cambridge", "Massachusetts", "US")
    assert parsed("Cambridge, UK") == ("Cambridge", None, "GB")


def test_upper_case_city_is_tidied_and_street_numbers_rejected():
    assert parsed("HAMBURG, GERMANY")[0] == "Hamburg"
    assert parsed("12 Main Street, Germany") == (None, None, "DE")


# ---------- feed values ----------

def test_normalize_country_accepts_codes_names_and_aliases():
    for value in ("US", "us", "USA", "United States", "United States of America", "U.S."):
        assert geo.normalize_country(value) == "US", value
    assert geo.normalize_country("Türkiye") == geo.normalize_country("Turkey") == "TR"
    assert geo.normalize_country("Czech Republic") == "CZ"
    assert geo.normalize_country("Germany") == "DE"
    for value in ("Georgia", "Atlantis", "", None, "X"):
        assert geo.normalize_country(value) is None, value


def test_normalize_region_expands_codes_only_in_context():
    assert geo.normalize_region("CA", "US") == "California"
    assert geo.normalize_region("ON", "CA") == "Ontario"
    assert geo.normalize_region("California", "US") == "California"
    assert geo.normalize_region("Île-de-France", "FR") == "Île-de-France"   # kept as the feed gave it
    assert geo.normalize_region("  ", "US") is None


# ---------- region groups ----------

def test_region_groups_resolve_members_recursively():
    emea = geo.region_group_countries("EMEA")
    assert {"DE", "GB", "AE", "ZA"} <= emea and "US" not in emea
    assert geo.region_group_countries("DACH") == {"DE", "AT", "CH"}
    assert "GB" not in geo.region_group_countries("EU")           # post-Brexit
    assert {"US", "CA", "BR"} <= geo.region_group_countries("AMERICAS")
    assert geo.find_region_group("emea") == "EMEA"
    assert geo.find_region_group("Asia Pacific") == "APAC"
    assert geo.find_region_group("berlin") is None
    assert ("EMEA", "Europe, Middle East & Africa") in geo.region_groups()
    assert all(not key.endswith(("EUROPE", "_EAST")) for key, _ in geo.region_groups())


# ---------- search expansion ----------

def test_expand_city_alias():
    terms = {p.lower() for c, p in geo.expand_term("nyc") if c is None}
    assert {"nyc", "new york", "new york city"} <= terms
    assert "sf" not in terms


def test_expand_country_adds_a_country_column_match():
    alternatives = geo.expand_term("germany")
    assert (None, "germany") == alternatives[0]
    assert ("country", "DE") in alternatives


def test_expand_bay_area_covers_the_metro():
    terms = {p.lower() for c, p in geo.expand_term("bay area")}
    assert {"san francisco", "palo alto", "mountain view"} <= terms


def test_expand_region_group():
    alternatives = geo.expand_term("EMEA")
    assert ("country", "DE") in alternatives and (None, "EMEA") in alternatives


def test_expand_does_not_guess_ambiguous_or_unknown_words():
    assert geo.expand_term("engineer") == [(None, "engineer")]
    assert geo.expand_term("paris") == [(None, "paris")]            # ambiguous city: no country guessed
    assert geo.expand_term("georgia") == [(None, "georgia")]
    assert geo.expand_term("") == [(None, "")]


def test_data_files_are_consistent():
    data = geo._load("city_aliases.json")
    for entry in data["cities"]:
        assert geo.country_name(entry["country"]), entry
    regions = geo._load("regions.json")
    codes = set(geo._load("countries.json"))
    for key, entry in regions.items():
        assert set(entry.get("countries", [])) <= codes, key
        for child in entry.get("includes", []):
            assert child in regions, (key, child)
