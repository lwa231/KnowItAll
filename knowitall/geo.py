"""Location understanding from small curated files (no geo library, no network).

Principle: a wrong country is worse than none. Structure supplied by an ATS feed always wins;
free-text locations are parsed only when the reading is unambiguous, and anything else stays
null (it is still found by text search). Data lives in knowitall/data/:

  countries.json      ISO-2 -> [name, aliases...]
  subdivisions.json   US states and Canadian provinces, code -> name
  city_aliases.json   ~230 major cities with aliases (NYC, SF...), an `ambiguous` flag for names shared
                      across countries (Paris TX, Dublin CA, Cambridge MA/UK), and metro areas
  regions.json        groups such as EMEA, APAC, DACH, EU with their member countries
"""
import json
import re
import unicodedata
from functools import lru_cache

from . import paths

DATA_DIR = paths.asset_dir() / "knowitall" / "data"

# "georgia" is a country and a US state: only read it as a state next to a Georgia city.
NEEDS_CITY = {"georgia"}
# "washington" alone might be the state or the capital: needs a city or other context.
NEEDS_CONTEXT = {"washington"}

WORKPLACE_WORDS = re.compile(
    r"\b(?:fully\s+|100%\s+)?(?:remote|hybrid|on-?site|in[- ]office|work\s+from\s+home|wfh|telecommute|anywhere)\b"
    r"(?:[- ]first)?", re.I)
SEGMENT_SPLIT = re.compile(r"\s*(?:\||;|/|•|·)\s*")
MORE_SUFFIX = re.compile(r"\(\+\d+\s+more\)", re.I)
TOKEN_SPLIT = re.compile(r"\s*(?:,|\(|\)|–|—)\s*|\s+-\s+")
LEADING_FILLER = re.compile(r"^(?:in|within|from|across|based in|located in|the)\s+", re.I)


def _load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def fold(text):
    """Case/accent/punctuation-insensitive form used for every lookup."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace(".", "").replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", text).strip()


# ---------- data indexes ----------

@lru_cache(maxsize=1)
def _countries():
    raw = _load("countries.json")
    names = {}
    for iso, aliases in raw.items():
        for alias in aliases:
            names.setdefault(fold(alias), iso)
    return {"codes": set(raw), "names": names, "canonical": {iso: aliases[0] for iso, aliases in raw.items()}}


@lru_cache(maxsize=1)
def _subdivisions():
    raw = _load("subdivisions.json")
    codes = {c: dict(table) for c, table in raw.items()}                       # country -> code -> name
    names = {}                                                                 # folded name -> (country, name)
    for country, table in raw.items():
        for name in table.values():
            names.setdefault(fold(name), (country, name))
    return {"codes": codes, "names": names}


@lru_cache(maxsize=1)
def _cities():
    raw = _load("city_aliases.json")
    by_name = {}
    for entry in raw["cities"]:
        for label in [entry["name"], *entry.get("aliases", [])]:
            by_name.setdefault(fold(label), []).append(entry)
    metros = {fold(k): v for k, v in raw["metros"].items()}
    return {"by_name": by_name, "metros": metros, "entries": raw["cities"]}


@lru_cache(maxsize=1)
def _regions():
    return _load("regions.json")


# ---------- countries and subdivisions ----------

def normalize_country(value):
    """ISO-2 for a feed value ('US', 'us', 'USA', 'United States'), or None."""
    text = str(value or "").strip()
    if not text:
        return None
    countries = _countries()
    if len(text) == 2 and text.upper() in countries["codes"]:
        return text.upper()
    folded = fold(text)
    if folded in NEEDS_CITY:
        return None
    return countries["names"].get(folded)


def all_countries():
    """Every country we know, [{code, name}] sorted by name: the choices the Region filter offers before anything is scanned."""
    return sorted(({"code": code, "name": country_name(code) or code} for code in _countries()["codes"]), key=lambda c: fold(c["name"]))


def country_name(iso):
    return _countries()["canonical"].get(iso)


def normalize_region(value, country=None):
    """Full name for a feed's region: 'CA' + US -> 'California'; unknown regions are kept as given."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None
    table = _subdivisions()["codes"].get(country or "", {})
    if text.upper() in table and len(text) <= 3:
        return table[text.upper()]
    hit = _subdivisions()["names"].get(fold(text))
    if hit and (country is None or hit[0] == country):
        return hit[1]
    return text


def subdivision_name(country, code):
    """'California' for ('US', 'CA'); None when that country has no such code in our tables."""
    return _subdivisions()["codes"].get(country or "", {}).get(str(code or "").upper())


def is_subdivision_code(code):
    return _subdivision_by_code(str(code or "")) is not None


def _is_code(token):
    return len(token) == 2 and token.isalpha() and token.isupper()


def _subdivision_by_code(token):
    """('US', 'California') for 'CA' etc. US states take priority over Canadian provinces."""
    for country in ("US", "CA"):
        name = _subdivisions()["codes"][country].get(token)
        if name:
            return country, name
    return None


def _city_entries(token):
    return _cities()["by_name"].get(fold(token), [])


# ---------- parsing free text ----------

def _tokens(segment):
    segment = WORKPLACE_WORDS.sub(" ", MORE_SUFFIX.sub(" ", segment))
    tokens = []
    for raw in TOKEN_SPLIT.split(segment):
        token = LEADING_FILLER.sub("", (raw or "").strip(" -–—.")).strip()
        if token:
            tokens.append(token)
    return tokens[-3:]


def _compatible(entries, country, region):
    return [e for e in entries
            if (country is None or e["country"] == country)
            and (region is None or e["region"] is None or e["region"] == region)]


def _tidy_city(token):
    if any(ch.isdigit() for ch in token) or len(token) > 60:
        return None
    return token.title() if token.isupper() and len(token) > 3 else token


def _whole_city(segment):
    """A city whose own name contains a comma or dots: 'Washington, D.C.', 'New York, NY'."""
    text = fold(WORKPLACE_WORDS.sub(" ", MORE_SUFFIX.sub(" ", segment)).strip(" -–—"))
    entries = [e for e in _cities()["by_name"].get(text, []) if not e.get("ambiguous")]
    if len({(e["name"], e["country"]) for e in entries}) == 1:
        entry = entries[0]
        return {"city": entry["name"], "region": entry["region"], "country": entry["country"]}
    return None


def _country_at_start(token):
    """Country named first ('Israel, Yokneam'; 'US, CA, Santa Clara'): unambiguous names and codes only."""
    if fold(token) in NEEDS_CITY:
        return None
    if _is_code(token):
        return token if token in _countries()["codes"] and not _subdivision_by_code(token) else None
    return _countries()["names"].get(fold(token))


def _parse_reversed(tokens):
    """'Country, [Region,] City' - the order some Workday tenants use. Only tried when the usual
    'City, Country' reading found nothing, and only when the first token is clearly a country."""
    if len(tokens) < 2:
        return None
    country = _country_at_start(tokens[0])
    if not country:
        return None
    rest = tokens[1:]
    region = None
    table = _subdivisions()["codes"].get(country, {})
    names = {fold(name): name for name in table.values()}
    first = rest[0]
    if _is_code(first) and first in table:
        region = table[first]
    elif fold(first) in names:
        region = names[fold(first)]
    if region:
        rest = rest[1:]
    city = None
    if rest:
        token = rest[-1]
        entries = _compatible(_city_entries(token), country, region)
        if len({e["name"] for e in entries}) == 1:
            city = entries[0]["name"]
        else:
            city = _tidy_city(token)
    if not (city or region or country):
        return None
    return {"city": city, "region": region, "country": country}


def _parse_segment(segment):
    tokens = _tokens(segment)
    if not tokens:
        return None
    return _whole_city(segment) or _parse_forward(tokens) or _parse_reversed(tokens)


def _parse_forward(tokens):
    multi = len(tokens) > 1
    country = region = None
    rest = list(tokens)
    tail = rest[-1]
    folded_tail = fold(tail)

    # -- the last token: a country, a US/CA subdivision, or nothing we can trust
    iso = None
    if folded_tail in NEEDS_CITY:
        if multi and any(e["region"] == "Georgia" and e["country"] == "US" for e in _city_entries(rest[0])):
            country, region = "US", "Georgia"
            rest.pop()
        else:
            return None
    elif _is_code(tail) and multi:
        sub = _subdivision_by_code(tail)
        collides = tail in _countries()["codes"]
        if sub and not collides:
            country, region = sub
            rest.pop()
        elif sub and collides:
            entries = _city_entries(rest[0])
            if any(e["country"] == sub[0] and e["region"] == sub[1] for e in entries):
                country, region = sub
                rest.pop()
            elif any(e["country"] == tail for e in entries):
                country = tail
                rest.pop()
            else:
                return None                                     # 'Wilmington, DE': Delaware or Germany?
        elif tail in _countries()["codes"]:
            country = tail
            rest.pop()
        elif _countries()["names"].get(folded_tail):        # 'UK', 'UAE': aliases, not ISO codes
            country = _countries()["names"][folded_tail]
            rest.pop()
    else:
        iso = _countries()["names"].get(folded_tail)
        if iso is None and _is_code(tail) and not multi:
            iso = tail if tail in _countries()["codes"] and not _subdivision_by_code(tail) else None
        if iso:
            country = iso
            rest.pop()
        elif folded_tail in _subdivisions()["names"]:
            if folded_tail in NEEDS_CONTEXT and not multi:
                return None
            country, region = _subdivisions()["names"][folded_tail]
            rest.pop()

    # -- a subdivision before a country: 'San Francisco, CA, United States' / 'Austin, Texas, USA'
    if country and region is None and rest:
        before = rest[-1]
        table = _subdivisions()["codes"].get(country, {})
        if _is_code(before) and before in table:
            region = table[before]
            rest.pop()
        elif fold(before) in _subdivisions()["names"] and _subdivisions()["names"][fold(before)][0] == country:
            region = _subdivisions()["names"][fold(before)][1]
            rest.pop()

    # -- the city
    city = None
    if not rest and not multi and country:
        for entry in _city_entries(tail):                   # city-states: 'Singapore', 'Hong Kong'
            if entry["country"] == country and fold(entry["name"]) == folded_tail and not entry.get("ambiguous"):
                city = entry["name"]
    if rest:
        token = rest[0]
        entries = _compatible(_city_entries(token), country, region)
        if country is None and region is None:
            entries = [e for e in entries if not e.get("ambiguous")]
        if len(entries) == 1 or (entries and len({(e["name"], e["country"]) for e in entries}) == 1):
            entry = entries[0]
            city = entry["name"]
            country = country or entry["country"]
            if region is None and entry["region"] and not entry.get("ambiguous"):
                region = entry["region"]
            elif region is None and entry["region"] and country == entry["country"] and len(rest) == 1 and multi:
                region = entry["region"]
        elif country:
            city = _tidy_city(token)                            # 'Kraków, Poland': not in the table, but clearly a city

    if not (city or region or country):
        return None
    return {"city": city, "region": region, "country": country}


def parse_location(text):
    """First confidently understood place in a free-text location, or None.

    Returns {'city', 'region', 'country'} (any may be None). Several places joined with ' | ' or ';'
    yield the first one that resolves.
    """
    for segment in SEGMENT_SPLIT.split(str(text or "")):
        parsed = _parse_segment(segment)
        if parsed:
            return parsed
    return None


# ---------- region groups (EMEA, APAC, DACH, EU...) ----------

def _group_countries(key, seen=()):
    entry = _regions().get(key) or {}
    result = set(entry.get("countries", []))
    for child in entry.get("includes", []):
        if child not in seen:
            result |= _group_countries(child, seen + (key,))
    return result


@lru_cache(maxsize=None)
def region_group_countries(key):
    return frozenset(_group_countries(key))


def region_groups():
    """[(key, label)] for the groups users can filter by (internal building blocks excluded)."""
    return [(key, entry["label"]) for key, entry in _regions().items() if entry.get("label")]


def region_group_aliases(key):
    return list((_regions().get(key) or {}).get("aliases", [])) or [key]


def find_region_group(text):
    folded = fold(text)
    for key, entry in _regions().items():
        if not entry.get("label"):
            continue
        if folded == fold(key) or folded in {fold(a) for a in entry.get("aliases", [])}:
            return key
    return None


# ---------- search-term expansion ----------

def is_known_place(text):
    """Is this (possibly multi-word) text a place name we can expand, like 'bay area' or 'new york'?"""
    return len(expand_term(text)) > 1


def expand_term(term):
    """Alternatives for one search word, as (column | None, phrase) pairs - the term itself first.

    'nyc' also finds 'New York'; 'germany' also finds rows whose country is DE; 'bay area' finds
    the cities around San Francisco. Nothing is guessed: only entries in the curated files expand.
    """
    folded = fold(term)
    alternatives = [(None, term)]
    if not folded:
        return alternatives

    def add(column, phrase):
        if (column, phrase) not in alternatives and (column, phrase.lower()) not in [(c, p.lower()) for c, p in alternatives]:
            alternatives.append((column, phrase))

    entries = _cities()["by_name"].get(folded, [])
    if len({e["name"] for e in entries}) == 1:
        entry = entries[0]
        for label in [entry["name"], *entry.get("aliases", [])]:
            if len(label) > 2 and "," not in label:
                add(None, label)
    for city in _cities()["metros"].get(folded, []):
        add(None, city)
    iso = _countries()["names"].get(folded) if folded not in NEEDS_CITY else None
    if iso:
        add("country", iso)
        add(None, _countries()["canonical"][iso])
    group = find_region_group(term)
    if group:
        for iso in sorted(region_group_countries(group)):
            add("country", iso)
        for alias in region_group_aliases(group):
            add(None, alias)
    return alternatives
