"""Turn a company web address into its most likely careers page(s)."""
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from botasaurus.sitemap import Filters, Sitemap

from .fetch import (
    BLOCKED_STATUS, _run_with_deadline, fetch_page, fetch_pages, is_blocked, needs_browser, page_ok, page_title,
    render_page, soup_of,
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
# /careers, /en/careers, /us/en/careers: up to two locale segments in front
EXACT_CAREERS_PATH_RE = re.compile(r"^(/[a-z]{2}([-_][a-z]{2})?){0,2}/(careers?|jobs)/?$", re.I)
LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$", re.I)
ATS_HOST_RE = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com", re.I)
# Two-part public suffixes like co.uk / com.au, so "shop.example.co.uk" -> "example.co.uk".
SECOND_LEVEL_LABELS = {"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc"}


MAX_HOMEPAGE_LINKS = 10
MAX_CANDIDATES = 3
OFFSITE_MIN_SCORE = 20      # a link to another domain must say "Careers"/"Jobs" in words (or in path and words)
SITEMAP_SECONDS = 30
# Job boards and social sites are not the company's own careers site, so a link to them is not followed.
JOB_BOARD_HOSTS = {"linkedin.com": "LinkedIn", "indeed.com": "Indeed", "glassdoor.com": "Glassdoor",
                   "ziprecruiter.com": "ZipRecruiter", "monster.com": "Monster"}
SOCIAL_HOSTS = {"facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com", "youtu.be"}


@dataclass
class Site:
    input_url: str
    root_url: str
    host: str
    domain: str   # registrable domain, e.g. airbnb.com
    slug: str     # e.g. airbnb (used to guess ATS board names)
    name: str     # display name, e.g. Airbnb
    # Domains whose pages count as this company's own: its domain, plus any careers site it hands over to
    # (chase.com -> careers.jpmorgan.com). Grows while the careers page is being found.
    careers_domains: set = field(default_factory=set)

    def __post_init__(self):
        self.careers_domains = set(self.careers_domains) | {self.domain}


def registrable_domain(host):
    """The domain a host belongs to: careers.shop.example.co.uk -> example.co.uk."""
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL_LABELS and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def parse_site(raw):
    raw = raw.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    parts = urlparse(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if "." not in host:
        raise ValueError(f"not a valid web address: {raw}")
    domain = registrable_domain(host)
    label = domain.split(".")[0]
    # Always https: botasaurus' HTTP client breaks on some http->https redirects (e.g. figma.com).
    input_url = parts._replace(scheme="https").geturl()
    return Site(input_url, f"https://{host}/", host, domain, re.sub(r"[^a-z0-9]", "", label), label.capitalize())


def same_site(host, domain):
    host = (host or "").lower()
    return host == domain or host.endswith("." + domain)


def on_site(host, site):
    """Is this host the company's own: its domain, or a careers site it has been found to hand over to?"""
    return any(same_site(host, domain) for domain in site.careers_domains)


def _denied(host):
    """(is it a site we never follow, the job board's name or None)."""
    domain = registrable_domain(host)
    if domain in JOB_BOARD_HOSTS:
        return True, JOB_BOARD_HOSTS[domain]
    return domain in SOCIAL_HOSTS, None


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


def homepage_links(home, site, ctx=None):
    """Careers-looking links on the homepage, best first: {url: (score, reason)}.

    A link to another domain is followed only when it clearly says "Careers"/"Jobs" (chase.com's only careers
    link goes to careers.jpmorgan.com). Job boards and social sites are never followed; a company whose only
    careers link goes to one of the job boards is noted, so the scan can say so."""
    found = {}
    if not page_ok(home):
        return found
    for a in soup_of(home).find_all("a", href=True):
        url = urljoin(home["final_url"], a["href"]).split("#")[0]
        parts = urlparse(url)
        if parts.scheme not in ("http", "https"):
            continue
        text = re.sub(r"\s+", " ", a.get_text(" ")).strip()[:80]
        score = _link_score(parts.path + ("?" + parts.query if parts.query else ""), text)
        if not on_site(parts.hostname, site):
            if score < OFFSITE_MIN_SCORE:
                continue                              # ATS links are otherwise picked up from the homepage HTML
            denied, board = _denied(parts.hostname)
            if denied:
                if board and ctx:
                    ctx.note("job_board", board)
                continue
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


def _non_english_locale(path):
    """Does the path start with a locale that is not English? /de/, /fr-ca/ and /jp/ are; /en/, /us/en/ and
    /en-gb/ are not (the second segment of /<country>/<language>/ is the language)."""
    segments = []
    for segment in path.split("/")[1:3]:
        if not LOCALE_SEGMENT_RE.match(segment):
            break
        segments.append(segment.lower())
    if not segments:
        return False
    return not any(re.split(r"[-_]", segment)[0] == "en" for segment in segments)


def _final_score(page, base_score):
    parts = urlparse(page["final_url"])
    score = base_score
    if ATS_HOST_RE.search(parts.hostname or ""):
        score += 60
    if EXACT_CAREERS_PATH_RE.match(parts.path):
        score += 50
    if (parts.hostname or "").startswith(("careers.", "jobs.")):
        score += 45
    if _non_english_locale(parts.path):
        score -= 40
    score -= 3 * len([seg for seg in parts.path.split("/") if seg])
    return score


def _sitemap_urls(ctx, site):
    try:
        links = _run_with_deadline(
            lambda: Sitemap(site.root_url, cache=ctx.config.cache is True)
            .filter(Filters.any_segment_equals(["careers", "career", "jobs", "job"]))
            .links(),
            seconds=SITEMAP_SECONDS, should_cancel=ctx.should_stop,
        )
    except Exception as error:
        ctx.log(f"sitemap lookup failed: {type(error).__name__}")
        return []
    return sorted(links or [], key=lambda url: (len(urlparse(url).path.split("/")), len(url)))[:5]


def discover(ctx, site):
    """Return (homepage, candidates). Candidates are page dicts with 'score' and 'reasons', best first.

    What went wrong along the way (a careers page that stayed blocked, a link to a job board only) is noted on
    the context, for the scan's final outcome."""
    ctx.phase("Finding the careers page…")
    home = fetch_page(ctx, site.root_url)
    if not page_ok(home) and not site.host.startswith("www."):
        www_home = fetch_page(ctx, f"https://www.{site.host}/")
        if page_ok(www_home):
            home = www_home
    if unreachable(home):
        return home, []
    if ctx.config.use_browser and needs_browser(home):
        ctx.log("homepage is blocked or JavaScript-only; loading it in Chrome")
        rendered = render_page(ctx, site.root_url)
        if page_ok(rendered):
            home = rendered
    if page_ok(home):
        ctx.log(f"homepage: {home['final_url']} ({home['status']})")
    else:
        ctx.log(f"homepage could not be loaded ({home.get('status')} {home.get('error') or ''})".strip())

    to_fetch = {}  # url -> (base score, reason)

    def want(url, score, reason):
        if score > to_fetch.get(url, (-1, ""))[0]:
            to_fetch[url] = (score, reason)

    if urlparse(site.input_url).path.strip("/"):
        want(site.input_url, 80, "address you entered")
    for url, (score, reason) in homepage_links(home, site, ctx).items():
        want(url, score, reason)
    root = home["final_url"] if page_ok(home) else site.root_url
    for path in PROBE_PATHS:
        want(urljoin(root, "/" + path), 15, f"common path /{path}")
    for sub in ("careers", "jobs"):
        want(f"https://{sub}.{site.domain}/", 0, f"{sub}.{site.domain} subdomain")

    candidates = _collect(fetch_pages(ctx, list(to_fetch)), to_fetch, home, site)
    if not candidates and not ctx.should_stop():
        ctx.log("no careers page found via links or common paths; checking the sitemap")
        sitemap_urls = _sitemap_urls(ctx, site)
        candidates = _collect(fetch_pages(ctx, sitemap_urls), {u: (5, "sitemap") for u in sitemap_urls}, home, site)

    candidates = candidates[:MAX_CANDIDATES]
    for i, candidate in enumerate(candidates):
        if candidate.get("blocked") and ctx.config.use_browser and not ctx.should_stop():
            ctx.log(f"{candidate['final_url']} refused plain requests ({candidate['status']}); loading it in Chrome")
            rendered = render_page(ctx, candidate["final_url"])
            if page_ok(rendered):
                candidates[i] = {**rendered, "score": candidate["score"], "reasons": candidate["reasons"]}
    for candidate in candidates:
        if not page_ok(candidate):
            ctx.note("blocked", candidate["final_url"])            # a careers page the site would not show us
    return home, [c for c in candidates if page_ok(c)]


def unreachable(home):
    return home.get("error") == "host does not resolve"


def _collect(pages, to_fetch, home, site):
    merged = {}
    for page in pages:
        base_score, reason = to_fetch.get(page["url"], (0, "?"))
        asked_for = reason.startswith(("homepage link", "address you entered"))     # the site itself pointed here
        # A careers URL that refuses plain HTTP (bot protection) is kept and retried in Chrome later.
        blocked = is_blocked(page) and (asked_for or bool(CAREERS_PATH_RE.search(urlparse(page["final_url"]).path)))
        if blocked:
            page = {**page, "blocked": True}
        elif not page_ok(page) or _looks_like_homepage(page, home) or NOT_FOUND_TITLE_RE.search(page_title(page)):
            continue
        final_host = urlparse(page["final_url"]).hostname
        on_ats = bool(ATS_HOST_RE.search(final_host or ""))
        looks_like_careers = CAREERS_TITLE_RE.search(page_title(page)) or CAREERS_PATH_RE.search(
            urlparse(page["final_url"]).path
        )
        if not on_site(final_host, site) and not on_ats:
            # Redirected to, or linked to, another domain. That is fine for a careers link, or a careers path that
            # lands on a page that looks like careers (chase.com -> careers.jpmorgan.com); never for a job board or
            # social site, and never for a page that does not look like careers at all. A page we could not read
            # (blocked) is judged by how we got there: only a link the site itself gave us counts.
            if _denied(final_host)[0] or not (asked_for if blocked else looks_like_careers):
                continue
            site.careers_domains.add(registrable_domain(final_host))
            page = {**page, "hosted_elsewhere": final_host}
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
