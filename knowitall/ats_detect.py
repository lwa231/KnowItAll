"""Detect which applicant tracking system (ATS) hosts a company's jobs, and the board token.

Patterns were checked against live sites on 2026-09-21 (see the plan for the list).
"""
import html as html_lib
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote

from .fetch import log


@dataclass
class Detection:
    ats: str
    token: str
    extra: dict = field(default_factory=dict)
    hits: int = 0
    guessed: bool = False

    def describe(self):
        how = "guessed from the company name" if self.guessed else f"found {self.hits}x on the careers pages"
        return f"{self.ats} board '{self.token}' ({how})"


def _simple(ats, group=1, lower=False):
    def build(match):
        token = unquote(match.group(group)).strip("._-")
        return Detection(ats, token.lower() if lower else token)
    return build


def _lever(match):
    region = "eu" if match.group(1) else ""
    return Detection("lever", match.group(2).strip("._-"), {"region": region})


def _workday(match):
    tenant, wd, site = match.group(1).lower(), match.group(2).lower(), match.group(3)
    return Detection("workday", f"{tenant}/{site}", {"tenant": tenant, "wd": wd, "site": site})


# Each entry: (compiled regex, builder(match) -> Detection)
PATTERNS = [
    # Greenhouse: embed scripts/iframes, direct API use, then hosted boards (job-boards./boards., incl. EU)
    (re.compile(r"greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?[^\s\"'<>]*?\bfor=([A-Za-z0-9_-]+)", re.I), _simple("greenhouse")),
    (re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I), _simple("greenhouse")),
    (re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)", re.I), _simple("greenhouse")),
    # Lever: hosted board and API (US and EU)
    (re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I), _lever),
    (re.compile(r"api\.(eu\.)?lever\.co/v0/postings/([A-Za-z0-9_.-]+)", re.I), _lever),
    # Ashby: hosted board and API; token is case-insensitive (Linear == linear)
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I), _simple("ashby", lower=True)),
    (re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)", re.I), _simple("ashby", lower=True)),
    # SmartRecruiters
    (re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I), _simple("smartrecruiters")),
    (re.compile(r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)", re.I), _simple("smartrecruiters")),
    # Workday: API path first (…/wday/cxs/{tenant}/{site}), then hosted board with optional locale segment
    (re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[a-z0-9_-]+/([A-Za-z0-9_-]+)", re.I), _workday),
    (re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([A-Za-z0-9_-]+)", re.I), _workday),
]

# Tokens that are URL furniture, not board names.
IGNORED_TOKENS = {
    "greenhouse": {"embed", "v1", "jobs"},
    "lever": {"api", "v0"},
    "ashby": {"api", "embed", "posting-api"},
    "smartrecruiters": {"static", "oneclick-ui", "v1"},
    "workday": set(),
}
IGNORED_WORKDAY_SITES = {"wday", "login", "job", "jobs", "details", "apply", "en-us"}

# ATS vendors we recognise but don't have an API module for yet (logged only).
UNSUPPORTED = {
    "eightfold": re.compile(r"eightfold\.ai|\beightfold\b", re.I),
    "icims": re.compile(r"icims\.com", re.I),
    "successfactors": re.compile(r"successfactors\.(com|eu)", re.I),
    "taleo": re.compile(r"taleo\.net", re.I),
    "phenom": re.compile(r"phenompeople\.com", re.I),
    "jobvite": re.compile(r"jobvite\.com", re.I),
    "workable": re.compile(r"apply\.workable\.com", re.I),
    "bamboohr": re.compile(r"[a-z0-9-]+\.bamboohr\.com/(careers|jobs)", re.I),
}
GH_JID_RE = re.compile(r"[?&]gh_jid=\d+", re.I)


def prepare(text):
    """Undo HTML entities and JSON escaping so URLs embedded in scripts/attributes can be matched."""
    text = html_lib.unescape(text or "")
    return text.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")


def _normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def names_match(name, slug):
    """Loose company-name check, e.g. 'Palantir Technologies' vs 'palantir', 'Ramp Jobs' vs 'ramp'."""
    name, slug = _normalized(name or ""), _normalized(slug or "")
    if len(name) < 3 or len(slug) < 3:
        return name == slug and bool(name)
    return slug in name or name in slug


def detect(pages, site):
    """Scan pages for ATS references.

    Returns (detections best-first, unsupported vendor names, saw_gh_jid).
    """
    counts = Counter()
    found = {}
    unsupported = set()
    saw_gh_jid = False
    for page in pages:
        if not page:
            continue
        text = prepare(f"{page.get('url', '')} {page.get('final_url', '')} {page.get('html', '')}")
        for pattern, build in PATTERNS:
            for match in pattern.finditer(text):
                detection = build(match)
                if not detection.token or detection.token.lower() in IGNORED_TOKENS[detection.ats]:
                    continue
                if detection.ats == "workday" and detection.extra["site"].lower() in IGNORED_WORKDAY_SITES:
                    continue
                key = (detection.ats, detection.token.lower())
                counts[key] += 1
                found.setdefault(key, detection)
        for vendor, pattern in UNSUPPORTED.items():
            if pattern.search(text):
                unsupported.add(vendor)
        saw_gh_jid = saw_gh_jid or bool(GH_JID_RE.search(text))

    detections = []
    for key, detection in found.items():
        detection.hits = counts[key]
        detections.append(detection)
    # Most mentions first, but a token that matches the company name beats everything
    # (careers pages sometimes link to partners' or portfolio companies' boards).
    detections.sort(key=lambda d: -(d.hits + (1000 if names_match(d.token.split("/")[0], site.slug) else 0)))
    for detection in detections[:3]:
        log(f"detected {detection.describe()}")
    return detections, unsupported, saw_gh_jid
