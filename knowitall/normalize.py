"""Common job schema, remote detection and de-duplication."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse

FIELDS = ["company", "title", "url", "location", "remote", "department", "posted", "source"]

REMOTE_RE = re.compile(r"\bremote\b", re.I)
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


def workplace_to_remote(value):
    """Map an ATS workplace value ("Remote", "Hybrid", "onsite", ...) to True/False/None."""
    text = (clean(value) or "").lower()
    if not text or text in {"unspecified", "unknown"}:
        return None
    if "hybrid" in text:
        return False
    if "remote" in text:
        return True
    return False  # on-site / in office


def make_job(company, title, url, location=None, remote=None, department=None, source=None, posted=None):
    title, location, department = clean(title), clean(location), clean(department)
    if remote is None and REMOTE_RE.search(f"{location or ''} {title or ''}"):
        remote = True  # never guess False: unknown stays None
    return {
        "company": clean(company),
        "title": title,
        "url": clean(url),
        "location": location,
        "remote": remote,
        "department": department,
        "posted": to_iso(posted),
        "source": source,
    }


def url_key(url):
    """Comparable form of a URL: no scheme/www/fragment/trailing slash; only gh_jid kept from the query."""
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k == "gh_jid"])
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


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
