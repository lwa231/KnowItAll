"""The KnowItAll pipeline: company web address -> job listings."""
import csv
import re
from collections import Counter
from urllib.parse import urlparse

from botasaurus import bt
from botasaurus.task import task

from . import generic
from .ats import GUESSABLE, MODULES
from .ats_detect import Detection, detect, names_match
from .discovery import discover, parse_site, same_site, unreachable
from .fetch import fetch_pages, log, page_ok, render_page, settings, should_stop, soup_of, visible_text_length
from .normalize import FIELDS, dedupe

SPA_SHELL_RE = re.compile(r'<div id="(root|app|__next)"[^>]*>\s*</div>', re.I)
BLOCKED_STATUS = {403, 429, 503}
STREAM_BATCH = 25   # rows handed to the UI at a time


def _try_detections(detections, site, limit=3):
    for detection in detections[:limit]:
        jobs = MODULES[detection.ats].fetch_jobs(detection, site)
        if jobs is not None:
            return detection, jobs
        log(f"{detection.ats} board '{detection.token}' returned no data; trying the next option")
    return None, None


def _guess_boards(site):
    """Try {slug} on ATSs that 404 cleanly for unknown boards, and verify the board is this company's."""
    for ats in GUESSABLE:
        detection = Detection(ats, site.slug, guessed=True)
        jobs = MODULES[ats].fetch_jobs(detection, site)
        if jobs is None:
            continue
        name = MODULES[ats].board_name(detection)
        on_company_site = any(same_site(urlparse(job["url"]).hostname, site.domain) for job in jobs)
        if names_match(name, site.slug) or on_company_site:
            log(f"{ats} has a board named '{site.slug}' (titled {name!r}); using it")
            return detection, jobs
        log(f"ignoring {ats} board '{site.slug}': its title {name!r} doesn't match {site.name}")
    return None, None


def _use_proper_company_name(jobs, result, site):
    """Prefer the company's own spelling from ATS/JSON-LD data ("ServiceNow") over the domain-derived one."""
    names = Counter(job["company"] for job in jobs if job["company"] and job["company"] != site.name)
    proper = next((name for name, _ in names.most_common() if names_match(name, site.slug)), None)
    if proper:
        result["company"] = proper
        for job in jobs:
            job["company"] = proper


def _needs_browser(page):
    if page.get("status") in BLOCKED_STATUS:
        return True
    if not page_ok(page):
        return False
    few_links = len(soup_of(page).find_all("a", href=True)) < 5
    return visible_text_length(page) < 2000 or bool(SPA_SHELL_RE.search(page["html"])) or few_links


def find_jobs(url, on_jobs=None):
    """Scrape one company.

    `on_jobs(list_of_jobs)` is called as results become available so a UI can stream them.
    Cancellation (fetch.settings.cancel_event) is checked between phases; whatever was found
    up to that point is kept and returned.
    """
    site = parse_site(url)
    log(f"===== {site.domain} =====")
    result = {
        "company": site.name,
        "domain": site.domain,
        "input": url,
        "careers_pages": [],
        "source": None,
        "notes": [],
        "jobs": [],
        "stopped": False,
    }

    home, candidates = discover(site)
    if should_stop():
        result["stopped"] = True
        return result
    if unreachable(home):
        log(f"{site.host} does not exist (DNS lookup failed); skipping")
        result["notes"].append("website could not be reached")
        return result
    result["careers_pages"] = [c["final_url"] for c in candidates]
    for candidate in candidates:
        log(f"careers page candidate: {candidate['final_url']} (score {candidate['score']}: {', '.join(candidate['reasons'][:2])})")

    # 1. A supported ATS referenced from the homepage / careers pages
    detections, unsupported, _ = detect([home] + candidates, site)
    detection, jobs = _try_detections(detections, site)

    # 2. One hop into "View all jobs" style links
    listing_pages = list(candidates)
    if jobs is None and not should_stop():
        hop = [p for p in fetch_pages(generic.view_all_links(candidates, site)) if page_ok(p)]
        if hop:
            log(f"followed {len(hop)} 'view all jobs' link(s)")
            listing_pages += hop
            hop_detections, hop_unsupported, _ = detect(hop, site)
            unsupported |= hop_unsupported
            detection, jobs = _try_detections(hop_detections, site)

    # 3. Guess the board by company name (e.g. stripe.com -> Greenhouse board 'stripe')
    if jobs is None and not should_stop():
        detection, jobs = _guess_boards(site)

    # 4. Generic HTML extraction (JSON-LD + job-looking links), then Chrome for JavaScript-rendered pages
    if jobs is None and not should_stop():
        if unsupported:
            note = f"detected {', '.join(sorted(unsupported))} (not supported yet), using generic extraction"
            log(note)
            result["notes"].append(note)
        jobs = generic.extract_jobs(listing_pages, site)
        if not jobs and settings.use_browser and listing_pages:
            targets = [p for p in listing_pages if _needs_browser(p)] or listing_pages[:1]
            log(f"no jobs in the plain HTML; rendering {targets[0]['final_url']} in Chrome")
            rendered = [p for p in (render_page(t["final_url"]) for t in targets[:2]) if page_ok(p)]
            rendered_detections, _, _ = detect(rendered, site)
            detection, ats_jobs = _try_detections(rendered_detections, site)
            jobs = ats_jobs if ats_jobs is not None else generic.extract_jobs(rendered, site)

    jobs = dedupe(jobs or [])
    _use_proper_company_name(jobs, result, site)
    result["jobs"] = jobs
    result["stopped"] = should_stop()
    if detection:
        result["source"] = detection.ats if not detection.guessed else f"{detection.ats} (by name)"
        result["source_detail"] = detection.describe()
    elif jobs:
        result["source"] = "page"
        result["source_detail"] = "generic extraction from the careers pages"
    if not candidates and not jobs:
        result["notes"].append("no careers page found")

    # Stream results out in batches so the UI fills in rather than appearing all at once.
    if on_jobs and jobs:
        for start in range(0, len(jobs), STREAM_BATCH):
            on_jobs(jobs[start:start + STREAM_BATCH])

    log(f"{site.domain}: {len(jobs)} job(s)")
    return result


def write_csv(jobs, path):
    # utf-8-sig so Excel on Windows shows accented characters correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(jobs)


def write_results(data, results):
    for result in results if isinstance(results, list) else [results]:
        if not result:
            continue
        name = f"jobs_{result['domain']}"
        bt.write_json(result["jobs"], name, log=False)
        write_csv(result["jobs"], f"output/{name}.csv")
        result["files"] = [f"output/{name}.json", f"output/{name}.csv"]


@task(output=write_results, close_on_crash=True, create_error_logs=False, raise_exception=False)
def scrape_jobs(url):
    try:
        return find_jobs(url)
    except ValueError as error:  # bad input address
        log(str(error))
        return None
