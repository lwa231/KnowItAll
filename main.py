"""KnowItAll: find a company's job listings from its web address.

Examples:
    python main.py stripe.com
    python main.py https://www.figma.com ramp.com --max-jobs 500
    python main.py stripe.com --no-history          # don't record the scan in the history database

This is a thin client of the same service the desktop app uses (knowitall.service), so a scan run here
is recorded, marked "new", and exported exactly like one started from the window.
"""
import argparse
import sys

from knowitall import paths


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Find job listings on a company's website.")
    parser.add_argument("urls", nargs="+", help="company web address(es), e.g. stripe.com")
    parser.add_argument("--no-cache", action="store_true", help="re-download everything instead of using cached pages")
    parser.add_argument("--headful", action="store_true", help="show the Chrome window when the browser fallback runs")
    parser.add_argument("--no-browser", action="store_true", help="never fall back to Chrome")
    parser.add_argument("--browser-workers", type=int, default=None, choices=(1, 2, 3),
                        help="Chrome instances that may render pages at once (default: the saved setting)")
    parser.add_argument("--max-jobs", type=int, default=2000, help="cap per company for very large boards (default 2000)")
    parser.add_argument("--max-enrich", type=int, default=50,
                        help="job pages to open for details in the generic fallback (default 50)")
    parser.add_argument("--parallel", type=int, default=1, help="companies to scrape at the same time (default 1)")
    parser.add_argument("--show", type=int, default=10, help="jobs to print per company (default 10)")
    parser.add_argument("--no-history", action="store_true",
                        help="do not record this scan in history.db (so nothing is marked 'new' either)")
    parser.add_argument("--no-export", action="store_true", help="do not write jobs_<domain>.json/.csv files")
    return parser.parse_args(argv)


def print_summary(service, show):
    print()
    for company in service.state()["companies"]:
        jobs = service.jobs_of(company["domain"])
        print(f"== {company['company']} ({company['domain']}) ==")
        print(f"   state        : {company['state']}")
        print(f"   careers page : {company.get('careers_page') or 'not found'}")
        print(f"   source       : {company.get('source_detail') or company.get('source') or '-'}")
        for note in company["notes"]:
            print(f"   note         : {note}")
        new = f"  ({company['new_count']} new)" if company.get("new_count") else ""
        print(f"   jobs         : {len(jobs)}{new}")
        for job in jobs[:show]:
            details = " | ".join(str(v) for v in (job["location"], job["department"]) if v)
            flag = f"  [{job['workplace']}]" if job.get("workplace") else ""
            print(f"     - {job['title']}" + (f"  ({details})" if details else "") + flag)
        if len(jobs) > show:
            print(f"     ... and {len(jobs) - show} more")
        print()


def main(argv=None):
    args = parse_args(argv)
    # history.db, cache/ and logs/ live in the per-user data folder, whatever the launch directory.
    paths.enter_data_dir()
    # Windows consoles can't print every character in job titles; replace instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from knowitall import compat, store
    from knowitall.browser import BrowserPool
    from knowitall.fetch import log
    from knowitall.service import Service

    compat.check_version(log)
    service = Service()
    pool = None if args.no_browser else BrowserPool(
        size=args.browser_workers or service.get_settings()["browser_workers"], headless=not args.headful)
    service.attach_browser(pool, headless=not args.headful)
    if pool and args.browser_workers:
        pool.resize(args.browser_workers)
    if not args.no_history:
        store.init()
        service.prune_in_background()

    options = {"fresh": args.no_cache, "max_jobs": args.max_jobs, "max_enrich": args.max_enrich,
               "concurrency": args.parallel, "history": not args.no_history, "autosave": not args.no_export}
    try:
        if not service.start(args.urls, options):
            print("nothing to scan: none of those look like web addresses", file=sys.stderr)
            return 2
        try:
            service.wait()
        except KeyboardInterrupt:
            service.stop()
            service.wait()
    finally:
        if pool:
            pool.close()

    print_summary(service, args.show)
    return 0 if any(service.jobs_of(c["domain"]) for c in service.state()["companies"]) else 1


if __name__ == "__main__":
    sys.exit(main())
