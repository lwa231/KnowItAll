"""The KnowItAll pipeline: company web address -> job listings."""
from collections import Counter
from urllib.parse import urlparse

from . import generic, json_jobs, outcomes
from .ats import GUESSABLE, LABELS, MODULES
from .ats_detect import Detection, detect, names_match, vendor_link
from .context import TIME_LIMIT
from .discovery import discover, on_site, parse_site, unreachable
from .fetch import fetch_pages, is_blocked, needs_browser, page_ok, render_page
from .normalize import url_key

STREAM_BATCH = 25   # rows handed to the UI at a time


def _try_detections(ctx, detections, site, limit=3):
    for detection in detections[:limit]:
        if ctx.should_stop():
            break
        ctx.phase(f"Reading the {LABELS[detection.ats]} job board…")
        jobs = MODULES[detection.ats].fetch_jobs(ctx, detection, site)
        if jobs is not None:
            return detection, jobs
        ctx.log(f"{detection.ats} board '{detection.token}' returned no data; trying the next option")
    return None, None


def _guess_boards(ctx, site):
    """Try {slug} on ATSs that 404 cleanly for unknown boards, and verify the board is this company's."""
    probe = ctx.with_on_jobs(None)          # a guess may be rejected below, so it must not stream rows out
    ctx.phase("Checking common job boards…")
    for ats in GUESSABLE:
        if ctx.should_stop():
            break
        detection = Detection(ats, site.slug, guessed=True)
        jobs = MODULES[ats].fetch_jobs(probe, detection, site)
        if jobs is None:
            continue
        name = MODULES[ats].board_name(ctx, detection)
        on_company_site = any(on_site(urlparse(job["url"]).hostname, site) for job in jobs)
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


def _extract_generic(ctx, pages, site, unsupported):
    """generic.extract_jobs, with one extra doubt: when the pages point to a hiring platform we cannot read, links
    that merely look like postings are not trusted. With no JobPosting data behind any of them they are site
    navigation ("Work with us"), and the honest answer is that the real listing is on that platform."""
    if not unsupported:
        return generic.extract_jobs(ctx, pages, site)
    held = []                                             # nothing is shown until we know they are postings
    jobs = generic.extract_jobs(ctx.with_on_jobs(held.extend), pages, site)
    if jobs and all(job.get("source") == "html-heuristic" for job in jobs):
        ctx.log(f"ignoring {len(jobs)} link(s) that only look like postings; the real listing is on "
                f"{', '.join(sorted(unsupported))}")
        return []
    return jobs


def _conclude(ctx, site, result, home=None, candidates=(), unsupported=(), pages=()):
    """Decide how this scan ended (see outcomes.py) and record it, with its wording, on the result."""
    count = len(result["jobs"])
    careers_url, platform, board = None, None, None
    if result["stopped"]:
        outcome = outcomes.TIMED_OUT if ctx.cancel.reason == TIME_LIMIT else outcomes.STOPPED
    elif count:
        outcome = outcomes.FOUND
    elif home is not None and unreachable(home):
        outcome = outcomes.UNREACHABLE
    elif unsupported:
        outcome, platform = outcomes.UNSUPPORTED, sorted(unsupported)[0]
        careers_url = vendor_link(pages, platform) or (candidates[0]["final_url"] if candidates else None)
    elif candidates:
        outcome, careers_url = outcomes.NO_LISTINGS, candidates[0]["final_url"]
    elif ctx.noted("blocked") or (home is not None and is_blocked(home)):
        outcome = outcomes.BLOCKED
        careers_url = (ctx.noted("blocked") or [site.root_url])[0]
    elif ctx.noted("job_board"):
        outcome, board = outcomes.NO_LISTINGS, ctx.noted("job_board")[0]
    else:
        outcome = outcomes.NO_CAREERS_PAGE
    if outcome in (outcomes.FOUND, outcomes.NO_LISTINGS) and candidates and not careers_url:
        careers_url = candidates[0]["final_url"]
    hosted = next((c["hosted_elsewhere"] for c in candidates if c.get("hosted_elsewhere")), None)
    message, hint = outcomes.describe(outcome, site.domain, count=count, platform=platform, board=board,
                                      limit=ctx.config.time_limit)
    result.update(outcome=outcome, outcome_detail=message, outcome_hint=hint, careers_url=careers_url,
                  outcome_short=outcomes.short(outcome, platform=platform, limit=ctx.config.time_limit, host=hosted))
    if hosted:
        result["careers_note"] = outcomes.careers_host_note(site.domain, hosted)
        result["notes"].append(result["careers_note"])
    return result


def find_jobs(ctx, url, on_jobs=None):
    """Scrape one company.

    `on_jobs(list_of_jobs)` is called as results become available so a UI can stream them.
    Cancellation (ctx.cancel) is checked between phases; whatever was found up to that point
    is kept and returned. The result always carries an `outcome` (see outcomes.py) saying how the scan ended.
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
        "outcome": None,
        "outcome_detail": None,
        "outcome_hint": None,
        "outcome_short": None,
        "careers_url": None,
        "careers_note": None,
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
    result["jobs"] = collected

    home, candidates = discover(ctx, site)
    if ctx.should_stop():
        result["stopped"] = True
        return _conclude(ctx, site, result, home, candidates)
    if unreachable(home):
        ctx.log(f"{site.host} does not exist (DNS lookup failed); skipping")
        result["notes"].append("website could not be reached")
        return _conclude(ctx, site, result, home)
    result["careers_pages"] = [c["final_url"] for c in candidates]
    for candidate in candidates:
        ctx.log(f"careers page candidate: {candidate['final_url']} (score {candidate['score']}: {', '.join(candidate['reasons'][:2])})")

    # 1. A supported ATS referenced from the homepage / careers pages
    detections, unsupported, _ = detect([home] + candidates, site)
    detection, jobs = _try_detections(ctx, detections, site)
    seen_pages = [home] + candidates

    # 2. One hop into "View all jobs" style links
    listing_pages = list(candidates)
    if jobs is None and not ctx.should_stop():
        hop = [p for p in fetch_pages(ctx, generic.view_all_links(candidates, site)) if page_ok(p)]
        if hop:
            ctx.log(f"followed {len(hop)} 'view all jobs' link(s)")
            listing_pages += hop
            seen_pages += hop
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
        jobs = _extract_generic(ctx, listing_pages, site, unsupported)
        if not jobs and ctx.config.use_browser and listing_pages and not ctx.should_stop():
            targets = [p for p in listing_pages if needs_browser(p, thorough=True)] or listing_pages[:1]
            ctx.log(f"no jobs in the plain HTML; rendering {targets[0]['final_url']} in Chrome")
            rendered_all = [render_page(ctx, t["final_url"], mode="listing",
                                        count_links=lambda html, t=t: generic.count_job_links(html, t["final_url"], site))
                            for t in targets[:2]]
            for target, page in zip(targets, rendered_all):
                if is_blocked(page):
                    ctx.note("blocked", target["final_url"])
            rendered = [p for p in rendered_all if page_ok(p)]
            seen_pages += rendered
            rendered_detections, rendered_unsupported, _ = detect(rendered, site)
            unsupported |= rendered_unsupported
            detection, ats_jobs = _try_detections(ctx, rendered_detections, site)
            jobs = ats_jobs if ats_jobs is not None else _extract_generic(ctx, rendered, site, unsupported)
            if not jobs and not ctx.should_stop():
                payloads = [payload for page in rendered for payload in (page.get("json_payloads") or [])]
                if payloads and ctx.config.debug_payloads:
                    json_jobs.log_payloads(ctx, payloads)
                if payloads:
                    ctx.phase("Reading the data the page loaded…")
                    jobs = json_jobs.extract(ctx, payloads, rendered[0]["final_url"], site)

    sink(jobs or [])              # whatever a reader returned without streaming; repeats are skipped
    jobs = collected
    result["stopped"] = ctx.should_stop()
    result["truncated"] = ctx.truncated
    if ctx.truncated:
        result["notes"].append(f"partial: {ctx.truncated}")
    if detection:
        result["source"] = detection.ats if not detection.guessed else f"{detection.ats} (by name)"
        result["source_detail"] = detection.describe()
    elif jobs:
        result["source"] = "json" if any(j.get("source") == "json-sniffed" for j in jobs) else "page"
        result["source_detail"] = ("read from data the careers page loaded" if result["source"] == "json"
                                   else "generic extraction from the careers pages")
    if not candidates and not jobs:
        result["notes"].append("no careers page found")

    ctx.log(f"{site.domain}: {len(jobs)} job(s)")
    return _conclude(ctx, site, result, home, candidates, unsupported, seen_pages)
