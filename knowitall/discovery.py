"""Turn a company web address into its most likely careers page(s)."""
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from botasaurus.sitemap import Filters, Sitemap

from .fetch import (
    _run_with_deadline, fetch_page, fetch_pages, log, page_ok, page_title, render_page, settings, soup_of,
)
from .normalize import url_key

# Link on the homepage pointing at a careers page (matched against the lowercased href / link text).
HREF_RE = re.compile(
    r"/(careers?|jobs?|join-?us|work-?with-?us|open-?(positions|roles)|opportunities|vacancies|hiring)(/|$|\?|\.html?)",
    re.I,
)
TEXT_RE = re.compile(
    r"\b(careers?|jobs|join us|join our team|work with us|open (positions|roles)|we'?re hiring)\b", re.I
)
PROBE_PATHS = ["careers", "careers/", "jobs", "careers/jobs", "company/careers", "about/careers", "join-us", "join"]
# A fetched page counts as a careers page if its <title> or final URL path says so.
CAREERS_TITLE_RE = re.compile(
    r"career|\bjobs?\b|positions|openings|hiring|vacanc|join (us|our team)|open roles|work (at|with) ", re.I
)
CAREERS_PATH_RE = re.compile(r"/(careers?|jobs?|vacancies|openings|positions)(/|$|\.html?)", re.I)
NOT_FOUND_TITLE_RE = re.compile(r"\b(404|not found|page not found|doesn'?t exist)\b", re.I)
EXACT_CAREERS_PATH_RE = re.compile(r"^(/[a-z]{2}([-_][a-z]{2})?)?/(careers?|jobs)/?$", re.I)
LOCALE_RE = re.compile(r"^/([a-z]{2})(?:[-_]([a-z]{2}))?(/|$)", re.I)
ATS_HOST_RE = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com", re.I)
# Two-part public suffixes like co.uk / com.au, so "shop.example.co.uk" -> "example.co.uk".
SECOND_LEVEL_LABELS = {"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc"}

BLOCKED_STATUS = {403, 429, 503}  # bot protection: worth retrying in a real browser

MAX_HOMEPAGE_LINKS = 10
MAX_CANDIDATES = 3


@dataclass
class Site:
    input_url: str
    root_url: str
    host: str
    domain: str   # registrable domain, e.g. airbnb.com
    slug: str     # e.g. airbnb (used to guess ATS board names)
    name: str     # display name, e.g. Airbnb


def parse_site(raw):
    raw = raw.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    parts = urlparse(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if "." not in host:
        raise ValueError(f"not a valid web address: {raw}")
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL_LABELS and len(labels[-1]) == 2:
        domain = ".".join(labels[-3:])
    else:
        domain = ".".join(labels[-2:])
    label = domain.split(".")[0]
    # Always https: botasaurus' HTTP client breaks on some http->https redirects (e.g. figma.com).
    input_url = parts._replace(scheme="https").geturl()
    return Site(input_url, f"https://{host}/", host, domain, re.sub(r"[^a-z0-9]", "", label), label.capitalize())


def same_site(host, domain):
    host = (host or "").lower()
    return host == domain or host.endswith("." + domain)


def _link_score(href_path, text):
    score = 0
    if HREF_RE.search(href_path.lower()):
        score += 10
    if text:
        if text.lower() in {"careers", "jobs", "career", "join us"}:
            score += 20
        elif TEXT_RE.search(text):
            score += 10
    return score


def homepage_links(home, site):
    """Careers-looking links on the homepage, best first: {url: (score, reason)}."""
    found = {}
    if not page_ok(home):
        return found
    for a in soup_of(home).find_all("a", href=True):
        url = urljoin(home["final_url"], a["href"]).split("#")[0]
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not same_site(parts.hostname, site.domain):
            continue  # off-site ATS links are picked up by ATS detection on the homepage HTML
        text = re.sub(r"\s+", " ", a.get_text(" ")).strip()[:80]
        score = _link_score(parts.path + ("?" + parts.query if parts.query else ""), text)
        if score and score > found.get(url, (0, ""))[0]:
            found[url] = (score, f"homepage link '{text or parts.path}'")
    best = sorted(found.items(), key=lambda item: -item[1][0])[:MAX_HOMEPAGE_LINKS]
    return dict(best)


def _looks_like_homepage(page, home):
    """Soft 404s: many sites answer every path with 200 and their homepage."""
    if not page_ok(home):
        return False
    if url_key(page["final_url"]) == url_key(home["final_url"]):
        return True
    same_title = page_title(page) and page_title(page) == page_title(home)
    similar_size = abs(len(page["html"]) - len(home["html"])) <= 0.03 * max(len(home["html"]), 1)
    return bool(same_title and similar_size)


def _final_score(page, base_score):
    parts = urlparse(page["final_url"])
    score = base_score
    if ATS_HOST_RE.search(parts.hostname or ""):
        score += 60
    if EXACT_CAREERS_PATH_RE.match(parts.path):
        score += 50
    if (parts.hostname or "").startswith(("careers.", "jobs.")):
        score += 45
    locale = LOCALE_RE.match(parts.path)
    if locale and locale.group(1).lower() != "en":
        score -= 40
    score -= 3 * len([seg for seg in parts.path.split("/") if seg])
    return score


def _sitemap_urls(site):
    try:
        links = _run_with_deadline(
            lambda: Sitemap(site.root_url, cache=settings.cache is True)
            .filter(Filters.any_segment_equals(["careers", "career", "jobs", "job"]))
            .links(),
            seconds=90,
        )
    except Exception as error:
        log(f"sitemap lookup failed: {type(error).__name__}")
        return []
    return sorted(links or [], key=lambda url: (len(urlparse(url).path.split("/")), len(url)))[:5]


def discover(site):
    """Return (homepage, candidates). Candidates are page dicts with 'score' and 'reasons', best first."""
    home = fetch_page(site.root_url)
    if not page_ok(home) and not site.host.startswith("www."):
        www_home = fetch_page(f"https://www.{site.host}/")
        if page_ok(www_home):
            home = www_home
    if unreachable(home):
        return home, []
    if settings.use_browser and _needs_browser(home):
        log("homepage is blocked or JavaScript-only; loading it in Chrome")
        rendered = render_page(site.root_url)
        if page_ok(rendered):
            home = rendered
    if page_ok(home):
        log(f"homepage: {home['final_url']} ({home['status']})")
    else:
        log(f"homepage could not be loaded ({home.get('status')} {home.get('error') or ''})".strip())

    to_fetch = {}  # url -> (base score, reason)

    def want(url, score, reason):
        if score > to_fetch.get(url, (-1, ""))[0]:
            to_fetch[url] = (score, reason)

    if urlparse(site.input_url).path.strip("/"):
        want(site.input_url, 80, "address you entered")
    for url, (score, reason) in homepage_links(home, site).items():
        want(url, score, reason)
    root = home["final_url"] if page_ok(home) else site.root_url
    for path in PROBE_PATHS:
        want(urljoin(root, "/" + path), 15, f"common path /{path}")
    for sub in ("careers", "jobs"):
        want(f"https://{sub}.{site.domain}/", 0, f"{sub}.{site.domain} subdomain")

    candidates = _collect(fetch_pages(list(to_fetch)), to_fetch, home, site)
    if not candidates:
        log("no careers page found via links or common paths; checking the sitemap")
        sitemap_urls = _sitemap_urls(site)
        candidates = _collect(fetch_pages(sitemap_urls), {u: (5, "sitemap") for u in sitemap_urls}, home, site)

    candidates = candidates[:MAX_CANDIDATES]
    for i, candidate in enumerate(candidates):
        if candidate.get("blocked") and settings.use_browser:
            log(f"{candidate['final_url']} refused plain requests ({candidate['status']}); loading it in Chrome")
            rendered = render_page(candidate["final_url"])
            if page_ok(rendered):
                candidates[i] = {**rendered, "score": candidate["score"], "reasons": candidate["reasons"]}
    return home, [c for c in candidates if page_ok(c)]


def unreachable(home):
    return home.get("error") == "host does not resolve"


def _needs_browser(home):
    if home.get("status") in BLOCKED_STATUS:
        return True
    return page_ok(home) and len(soup_of(home).find_all("a", href=True)) < 5


def _collect(pages, to_fetch, home, site):
    merged = {}
    for page in pages:
        # A careers URL that refuses plain HTTP (bot protection) is kept and retried in Chrome later.
        blocked = page.get("status") in BLOCKED_STATUS and bool(CAREERS_PATH_RE.search(urlparse(page["final_url"]).path))
        if blocked:
            page = {**page, "blocked": True}
        elif not page_ok(page) or _looks_like_homepage(page, home) or NOT_FOUND_TITLE_RE.search(page_title(page)):
            continue
        final_host = urlparse(page["final_url"]).hostname
        on_ats = bool(ATS_HOST_RE.search(final_host or ""))
        if not same_site(final_host, site.domain) and not on_ats:
            continue  # redirected somewhere unrelated
        base_score, reason = to_fetch.get(page["url"], (0, "?"))
        looks_like_careers = CAREERS_TITLE_RE.search(page_title(page)) or CAREERS_PATH_RE.search(
            urlparse(page["final_url"]).path
        )
        if not on_ats and not looks_like_careers and not blocked:
            continue
        key = url_key(page["final_url"])
        score = _final_score(page, base_score)
        if key in merged:
            existing = merged[key]
            existing["score"] = max(existing["score"], score) + 5
            existing["reasons"].append(reason)
        else:
            merged[key] = {**page, "score": score, "reasons": [reason]}
    return sorted(merged.values(), key=lambda page: -page["score"])
