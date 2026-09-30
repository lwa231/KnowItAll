"""Common job schema, workplace/employment/location normalisation and de-duplication."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse

from . import geo

# `remote` is derived from `workplace` (True for remote, False for hybrid/onsite, None if unknown) and is
# kept so older exports and consumers keep working. The last six fields arrived with structured filtering.
FIELDS = ["company", "title", "url", "location", "remote", "department", "posted", "source",
          "workplace", "employment_type", "city", "region", "country", "geo_confidence"]
WORKPLACES = ("remote", "hybrid", "onsite")
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "intern", "other")

REMOTE_RE = re.compile(r"\bremote\b", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
ONSITE_WORDS_RE = re.compile(r"on[- ]?site|in[- ]office|office[- ]based|in[- ]person", re.I)
INTERN_RE = re.compile(r"intern|co-?op|apprentice|working student|praktik", re.I)
PART_TIME_RE = re.compile(r"part[-_ ]?time", re.I)
FULL_TIME_RE = re.compile(r"full[-_ ]?time|permanent|regular|\bfte\b", re.I)
CONTRACT_RE = re.compile(r"contract|freelanc|fixed[- ]term|temp(?:orary)?\b|seasonal|consult", re.I)
# Workday reports relative text instead of a date, e.g. "Posted Today", "Posted 30+ Days Ago"
WORKDAY_POSTED_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def to_iso(value):
    """Normalise whatever a source calls a date into an ISO 8601 UTC string, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):           # Lever uses epoch milliseconds
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None

    relative = WORKDAY_POSTED_RE.search(text)
    if relative:
        word = relative.group(1).lower()
        days = 0 if word == "today" else 1 if word == "yesterday" else int(relative.group(2) or 0)
        # Day precision only: anchor to midnight so "Posted Today" never reads as "just now".
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return (midnight - timedelta(days=days)).isoformat()

    cleaned = text.replace("Z", "+00:00")
    for parse in (datetime.fromisoformat, lambda s: datetime.strptime(s, "%Y-%m-%d")):
        try:
            parsed = parse(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            continue
    return None


def clean(value):
    """Collapse whitespace; join lists; return None for empty values."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("label") or value.get("value")
    if isinstance(value, (list, tuple)):
        parts = [clean(item) for item in value]
        value = " | ".join(dict.fromkeys(part for part in parts if part))
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or None


def workplace_of(value):
    """Map an ATS workplace value ("Remote", "Hybrid", "on-site", "Flex", ...) to remote/hybrid/onsite/None."""
    text = (clean(value) or "").lower()
    if not text or text in {"unspecified", "unknown", "n/a", "none"}:
        return None
    if "hybrid" in text or text == "flex" or "flexible" in text:
        return "hybrid"
    if "remote" in text or "telecommute" in text or "work from home" in text:
        return "remote"
    if ONSITE_WORDS_RE.search(text) or text in {"office", "onsite", "on site"}:
        return "onsite"
    return None


def remote_of(workplace):
    return None if workplace is None else workplace == "remote"


def workplace_to_remote(value):
    """Map an ATS workplace value ("Remote", "Hybrid", "onsite", ...) to True/False/None."""
    return remote_of(workplace_of(value))


def employment_type_of(value):
    """Map an ATS employment type ("Full-time", "FULL_TIME", "Contractor", "Intern"...) to our vocabulary."""
    text = (clean(value) or "").strip()
    if not text or text.lower() in {"unspecified", "unknown", "n/a", "none"}:
        return None
    if INTERN_RE.search(text):
        return "intern"
    if PART_TIME_RE.search(text):
        return "part_time"
    if FULL_TIME_RE.search(text):
        return "full_time"
    if CONTRACT_RE.search(text):
        return "contract"
    return "other"


def _workplace_from_text(location, title):
    text = f"{location or ''} {title or ''}"
    remote, hybrid = bool(REMOTE_RE.search(text)), bool(HYBRID_RE.search(text))
    if remote and hybrid:
        return None                   # "Remote or Hybrid": not one thing, so unknown rather than a guess
    if hybrid:
        return "hybrid"
    if remote:
        return "remote"
    return None                       # never guess on-site: unknown stays None


def _structured_location(location, city, region, country):
    """Feed-supplied structure first; free text only fills what the feed left out, and never contradicts it."""
    feed_country = geo.normalize_country(country)
    feed_region = geo.normalize_region(region, feed_country)
    feed_city = clean(city)
    from_feed = bool(feed_country or feed_region or feed_city)
    parsed = geo.parse_location(location) or {}
    if from_feed:
        if parsed.get("country") in (None, feed_country) or feed_country is None:
            feed_country = feed_country or parsed.get("country")
            feed_region = feed_region or parsed.get("region")
            feed_city = feed_city or parsed.get("city")
        return feed_city, feed_region, feed_country, "feed"
    if parsed:
        return parsed["city"], parsed["region"], parsed["country"], "parsed"
    return None, None, None, None


def make_job(company, title, url, location=None, remote=None, department=None, source=None, posted=None,
             workplace=None, employment_type=None, city=None, region=None, country=None):
    title, location, department = clean(title), clean(location), clean(department)
    workplace = workplace if workplace in WORKPLACES else workplace_of(workplace)
    if workplace is None and remote is not None:
        workplace = "remote" if remote else "onsite"          # legacy callers that only know remote/not
    if workplace is None:
        workplace = _workplace_from_text(location, title)
    city, region, country, confidence = _structured_location(location, city, region, country)
    return {
        "company": clean(company),
        "title": title,
        "url": clean(url),
        "location": location,
        "remote": remote_of(workplace),
        "department": department,
        "posted": to_iso(posted),
        "source": source,
        "workplace": workplace,
        "employment_type": employment_type if employment_type in EMPLOYMENT_TYPES else employment_type_of(employment_type),
        "city": city,
        "region": region,
        "country": country,
        "geo_confidence": confidence,
    }


JOB_FRAGMENT_RE = re.compile(r"^job-[\w.~-]+$")


def url_key(url):
    """Comparable form of a URL: no scheme/www/trailing slash; only gh_jid kept from the query, and a
    #job-<id> fragment kept (postings with no page of their own are told apart by it; any other fragment is noise)."""
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k == "gh_jid"])
    fragment = parts.fragment if JOB_FRAGMENT_RE.match(parts.fragment or "") else ""
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "") + (f"#{fragment}" if fragment else "")


def dedupe(jobs):
    seen, unique = set(), []
    for job in jobs:
        if not job.get("title") or not job.get("url"):
            continue
        key = url_key(job["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique
