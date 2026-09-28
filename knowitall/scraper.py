"""The KnowItAll pipeline: company web address -> job listings."""
import re
from collections import Counter
from urllib.parse import urlparse

from . import generic
from .ats import GUESSABLE, MODULES
from .ats_detect import Detection, detect, names_match
from .discovery import discover, parse_site, same_site, unreachable
from .fetch import fetch_pages, needs_browser, page_ok, render_page
from .normalize import url_key

STREAM_BATCH = 25   # rows handed to the UI at a time


def _try_detections(ctx, detections, site, limit=3):
    for detection in detections[:limit]:
        jobs = MODULES[detection.ats].fetch_jobs(ctx, detection, site)
        if jobs is not None:
            return detection, jobs
        ctx.log(f"{detection.ats} board '{detection.token}' returned no data; trying the next option")
    return None, None


def _guess_boards(ctx, site):
    """Try {slug} on ATSs that 404 cleanly for unknown boards, and verify the board is this company's."""
    probe = ctx.with_on_jobs(None)          # a guess may be rejected below, so it must not stream rows out
    for ats in GUESSABLE:
        detection = Detection(ats, site.slug, guessed=True)
        jobs = MODULES[ats].fetch_jobs(probe, detection, site)
        if jobs is None:
            continue
        name = MODULES[ats].board_name(ctx, detection)
        on_company_site = any(same_site(urlparse(job["url"]).hostname, site.domain) for job in jobs)
        if names_match(name, site.slug) or on_company_site:
            ctx.log(f"{ats} has a board named '{site.slug}' (titled {name!r}); using it")
            return detection, jobs
        ctx.log(f"ignoring {ats} board '{site.slug}': its title {name!r} doesn't match {site.name}")
    return None, None


def _use_proper_company_name(jobs, result, site):
    """Prefer the company's own spelling from ATS/JSON-LD data ("ServiceNow") over the domain-derived one."""
    names = Counter(job["company"] for job in jobs if job["company"] and job["company"] != site.name)
    proper = next((name for name, _ in names.most_common() if names_match(name, site.slug)), None)
    if proper:
        result["company"] = proper
        for job in jobs:
            job["company"] = proper


def find_jobs(ctx, url, on_jobs=None):
    """Scrape one company.

    `on_jobs(list_of_jobs)` is called as results become available so a UI can stream them.
    Cancellation (ctx.cancel) is checked between phases; whatever was found up to that point
    is kept and returned.
    """
    site = parse_site(url)
    ctx.log(f"===== {site.domain} =====")
    result = {
        "company": site.name,
        "domain": site.domain,
        "input": url,
        "careers_pages": [],
        "source": None,
        "notes": [],
        "jobs": [],
        "stopped": False,
        "truncated": None,
    }

    # Every reader hands its results to `sink` page by page. It drops duplicates and rows without a
    # title or link, then passes the rest on immediately, so a slow company fills in as it goes.
    seen, collected = set(), []

    def sink(batch):
        fresh = []
        for job in batch:
            key = url_key(job.get("url") or "")
            if not job.get("title") or not job.get("url") or key in seen:
                continue
            seen.add(key)
            fresh.append(job)
        if not fresh:
            return
        _use_proper_company_name(fresh, result, site)
        collected.extend(fresh)
        if on_jobs:
            for start in range(0, len(fresh), STREAM_BATCH):
                on_jobs(fresh[start:start + STREAM_BATCH])

    ctx = ctx.with_on_jobs(sink)

    home, candidates = discover(ctx, site)
    if ctx.should_stop():
        result["stopped"] = True
        return result
    if unreachable(home):
        ctx.log(f"{site.host} does not exist (DNS lookup failed); skipping")
        result["notes"].append("website could not be reached")
        return result
    result["careers_pages"] = [c["final_url"] for c in candidates]
    for candidate in candidates:
        ctx.log(f"careers page candidate: {candidate['final_url']} (score {candidate['score']}: {', '.join(candidate['reasons'][:2])})")

    # 1. A supported ATS referenced from the homepage / careers pages
    detections, unsupported, _ = detect([home] + candidates, site)
    detection, jobs = _try_detections(ctx, detections, site)

    # 2. One hop into "View all jobs" style links
    listing_pages = list(candidates)
    if jobs is None and not ctx.should_stop():
        hop = [p for p in fetch_pages(ctx, generic.view_all_links(candidates, site)) if page_ok(p)]
        if hop:
            ctx.log(f"followed {len(hop)} 'view all jobs' link(s)")
            listing_pages += hop
            hop_detections, hop_unsupported, _ = detect(hop, site)
            unsupported |= hop_unsupported
            detection, jobs = _try_detections(ctx, hop_detections, site)

    # 3. Guess the board by company name (e.g. stripe.com -> Greenhouse board 'stripe')
    if jobs is None and not ctx.should_stop():
        detection, jobs = _guess_boards(ctx, site)

    # 4. Generic HTML extraction (JSON-LD + job-looking links), then Chrome for JavaScript-rendered pages
    if jobs is None and not ctx.should_stop():
        if unsupported:
            note = f"detected {', '.join(sorted(unsupported))} (not supported yet), using generic extraction"
            ctx.log(note)
            result["notes"].append(note)
        jobs = generic.extract_jobs(ctx, listing_pages, site)
        if not jobs and ctx.config.use_browser and listing_pages:
            targets = [p for p in listing_pages if needs_browser(p, thorough=True)] or listing_pages[:1]
            ctx.log(f"no jobs in the plain HTML; rendering {targets[0]['final_url']} in Chrome")
            rendered = [p for p in (render_page(ctx, t["final_url"]) for t in targets[:2]) if page_ok(p)]
            rendered_detections, _, _ = detect(rendered, site)
            detection, ats_jobs = _try_detections(ctx, rendered_detections, site)
            jobs = ats_jobs if ats_jobs is not None else generic.extract_jobs(ctx, rendered, site)

    sink(jobs or [])              # whatever a reader returned without streaming; repeats are skipped
    jobs = collected
    result["jobs"] = jobs
    result["stopped"] = ctx.should_stop()
    result["truncated"] = ctx.truncated
    if ctx.truncated:
        result["notes"].append(f"partial: {ctx.truncated}")
    if detection:
        result["source"] = detection.ats if not detection.guessed else f"{detection.ats} (by name)"
        result["source_detail"] = detection.describe()
    elif jobs:
        result["source"] = "page"
        result["source_detail"] = "generic extraction from the careers pages"
    if not candidates and not jobs:
        result["notes"].append("no careers page found")

    ctx.log(f"{site.domain}: {len(jobs)} job(s)")
    return result
