"""Fallback for companies with no supported ATS: read job listings from the careers pages' HTML."""
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from .discovery import same_site
from .fetch import fetch_pages, log, page_ok, settings, soup_of
from .normalize import clean, make_job, url_key

# A link whose path looks like a single job posting, e.g. /jobs/senior-engineer-1234 or ?gh_jid=123
JOB_PATH_RE = re.compile(
    r"/(jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|roles?|requisitions?|opportunit(?:y|ies))/([^/?#]{3,})",
    re.I,
)
JOB_QUERY_RE = re.compile(r"[?&](gh_jid|jobid|job_id|reqid|requisitionid)=", re.I)
# Second path segments that are navigation/category pages, not postings.
NAV_SEGMENTS = {
    "search", "results", "teams", "team", "departments", "department", "locations", "location", "benefits",
    "culture", "values", "perks", "faq", "faqs", "students", "university", "early-careers", "internships",
    "alerts", "job-alerts", "login", "sign-in", "signin", "saved", "category", "categories", "all", "page",
    "how-we-hire", "hiring-process", "interview", "interviewing", "apply", "privacy", "accessibility",
    "saved-jobs", "my-jobs", "job-cart", "search-jobs", "job-search", "all-jobs", "talent-community",
    "join-talent-community", "recommended-jobs",
}
NAV_PREFIXES = ("life-", "life_", "why-", "working-at", "work-at", "meet-", "our-")
VIEW_ALL_RE = re.compile(
    r"view (all )?(jobs|openings|positions|roles)|see (all )?open (roles|positions|jobs)|search (all )?jobs"
    r"|browse (all )?(jobs|roles|openings)|(current|all|open) (openings|positions|roles|jobs)",
    re.I,
)
# Bare nav labels count too, but only when the link points at a jobs-type path.
BARE_JOBS_TEXT_RE = re.compile(r"^(jobs|all jobs|job search|find jobs|openings|open positions|open roles|positions)$", re.I)
JOBS_PATH_RE = re.compile(r"/(jobs?|positions|openings|vacancies|roles|job-search|search-jobs)(/|$)", re.I)
PAGE_PARAM_RE = re.compile(r"^(page|p|pg|pagenum|page_num|pagenumber)$", re.I)
MAX_LISTING_PAGES = 40
GENERIC_LINK_TEXT = {
    "apply", "apply now", "learn more", "view job", "view", "read more", "see details", "details", "more",
    "view role", "see role", "view position", "view details", "see job", "careers", "jobs", "open positions",
}
# Utility links such as "Saved Jobs", "Search jobs", "Job alerts".
UTILITY_TITLE_RE = re.compile(
    r"^(saved|search|all|browse|view|my|find|recommended|similar|featured)\s+(jobs|roles|openings|positions)$"
    r"|^job (alerts?|search|cart)$|talent (community|network)",
    re.I,
)
LOCATION_RE = re.compile(
    r"\b(remote|hybrid|on-?site)\b|,\s*[A-Z]{2}\b|\b(United States|USA|United Kingdom|UK|Canada|Germany|France"
    r"|India|Australia|Singapore|Ireland|Netherlands|Japan|Brazil|Mexico|Spain|Poland|Israel|Europe|EMEA|APAC)\b"
)


# ---------- JSON-LD (schema.org JobPosting) ----------

def _walk_ld(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_ld(item)
    elif isinstance(node, dict):
        types = node.get("@type")
        if "JobPosting" in (types if isinstance(types, list) else [types]):
            yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _walk_ld(node[key])


def job_postings_in(page):
    # Not bt.extract_ld_json: it calls json.loads(tag.string), and tag.string is often None.
    postings = []
    for tag in soup_of(page).find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            continue
        postings.extend(_walk_ld(data))
    return postings


def _ld_location(posting):
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    names = []
    for place in places:
        address = place.get("address") if isinstance(place, dict) else place
        if isinstance(address, dict):
            country = address.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            names.append(", ".join(p for p in (address.get("addressLocality"), address.get("addressRegion"), country) if p))
        elif address:
            names.append(str(address))
    return names


def job_from_ld(posting, page_url, site):
    remote = True if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE" else None
    organization = posting.get("hiringOrganization")
    company = organization.get("name") if isinstance(organization, dict) else None
    url = posting.get("url")
    return make_job(
        company=company or site.name,
        title=posting.get("title") or posting.get("name"),
        url=urljoin(page_url, url) if isinstance(url, str) else page_url,
        location=_ld_location(posting),
        remote=remote,
        department=posting.get("occupationalCategory") or posting.get("industry"),
        source="json-ld",
    )


# ---------- link heuristics ----------

def _title_from_slug(path):
    slug = path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[-_]+", " ", re.sub(r"[-_]?\d{4,}$", "", slug)).strip()
    return slug.title() if len(slug) >= 3 else None


def _is_job_link(url, site, listing_url):
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not same_site(parts.hostname, site.domain):
        return False
    if url_key(url) == url_key(listing_url) or re.search(r"[?&]page=", url):
        return False
    match = JOB_PATH_RE.search(parts.path)
    if match:
        segment = match.group(2).lower()
        return segment not in NAV_SEGMENTS and not segment.startswith(NAV_PREFIXES)
    return bool(JOB_QUERY_RE.search("?" + parts.query))


def job_links(page, site):
    """[{url, title, location}] for links on a listing page that look like individual postings."""
    links = {}
    for a in soup_of(page).find_all("a", href=True):
        url = urljoin(page["final_url"], a["href"]).split("#")[0]
        if not _is_job_link(url, site, page["final_url"]):
            continue
        chunks = [c for c in (clean(s) for s in a.stripped_strings) if c]
        title = chunks[0] if chunks else None
        if not title or title.lower() in GENERIC_LINK_TEXT or not (3 <= len(title) <= 150):
            title = _title_from_slug(urlparse(url).path)
        if not title or UTILITY_TITLE_RE.search(title):
            continue
        location = next((c for c in chunks[1:] if LOCATION_RE.search(c)), None)
        links.setdefault(url_key(url), {"url": url, "title": title, "location": location})
    return list(links.values())


def view_all_links(pages, site):
    """URLs behind 'View all jobs' / 'See open roles' style links (one hop from the careers pages)."""
    urls = []
    for page in pages:
        if not page_ok(page):
            continue
        for a in soup_of(page).find_all("a", href=True):
            text = clean(a.get_text(" ")) or ""
            url = urljoin(page["final_url"], a["href"]).split("#")[0]
            if not url.startswith(("http://", "https://")):
                continue
            if url_key(url) == url_key(page["final_url"]) or len(text) > 60:
                continue
            if VIEW_ALL_RE.search(text) or (BARE_JOBS_TEXT_RE.match(text) and JOBS_PATH_RE.search(urlparse(url).path)):
                urls.append(url)
    return list(dict.fromkeys(urls))[:5]


def pagination_urls(page):
    """Other result pages of a paginated listing (?page=N style), e.g. careers.servicenow.com/jobs/?page=33."""
    base = urlparse(page["final_url"])
    param, template, last = None, None, 1
    for a in soup_of(page).find_all("a", href=True):
        parts = urlparse(urljoin(page["final_url"], a["href"]))
        if parts.hostname != base.hostname or parts.path.rstrip("/") != base.path.rstrip("/"):
            continue
        for key, value in parse_qsl(parts.query):
            if PAGE_PARAM_RE.match(key) and value.isdigit() and int(value) > last:
                param, template, last = key, parts, int(value)
    if not param:
        return []
    last = min(last, MAX_LISTING_PAGES)
    urls = []
    for number in range(2, last + 1):
        query = [(k, str(number) if k == param else v) for k, v in parse_qsl(template.query)]
        urls.append(template._replace(query=urlencode(query), fragment="").geturl())
    return urls


# ---------- entry point ----------

def extract_jobs(pages, site):
    """Jobs from listing pages: inline JSON-LD first, then job-looking links enriched with each job page's JSON-LD."""
    jobs = []
    candidates = {}
    pages = [p for p in pages if page_ok(p)]
    more_pages = []
    for page in pages:
        if len(job_links(page, site)) >= 3:
            more_pages += pagination_urls(page)
    if more_pages:
        log(f"listing is paginated; fetching {len(more_pages)} more result page(s)")
        pages += [p for p in fetch_pages(more_pages) if page_ok(p)]

    for page in pages:
        jobs.extend(job_from_ld(p, page["final_url"], site) for p in job_postings_in(page))
        for link in job_links(page, site):
            candidates.setdefault(url_key(link["url"]), link)
    if jobs:
        log(f"found {len(jobs)} JobPosting entries embedded in the careers pages")

    links = list(candidates.values())
    if not links:
        return jobs
    to_open = links[: settings.max_enrich]
    log(f"found {len(links)} job-looking links; opening {len(to_open)} to read their JobPosting data")
    detail_pages = fetch_pages([link["url"] for link in to_open])
    detail_by_key = {url_key(p["url"]): p for p in detail_pages if p}

    for link in links:
        detail = detail_by_key.get(url_key(link["url"]))
        postings = job_postings_in(detail) if page_ok(detail) else []
        if postings:
            jobs.append(job_from_ld(postings[0], detail["final_url"], site))
        else:
            jobs.append(make_job(site.name, link["title"], link["url"], link["location"], source="html-heuristic"))
    return jobs
