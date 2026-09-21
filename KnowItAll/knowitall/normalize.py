"""Common job schema, remote detection and de-duplication."""
import re
from urllib.parse import parse_qsl, urlencode, urlparse

FIELDS = ["company", "title", "url", "location", "remote", "department", "source"]

REMOTE_RE = re.compile(r"\bremote\b", re.I)


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


def make_job(company, title, url, location=None, remote=None, department=None, source=None):
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
