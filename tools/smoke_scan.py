"""Scan real companies through the same Service the app uses, and print how each one ended.

    python tools/smoke_scan.py stripe.com uber.com tesla.com chase.com nvidia.com
    python tools/smoke_scan.py uber.com --debug        # also log the JSON the page loaded (to build adapters)

For every company: its domain, outcome, postings found, and seconds taken. The run uses a throw-away data folder
(history, cache and settings of the real app are never read or written) unless --real-data is given. It needs the
network, and Google Chrome for the sites that only show their jobs to a browser.
"""
import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Scan companies and print domain, outcome, postings, seconds.")
    parser.add_argument("domains", nargs="+", help="company web addresses")
    parser.add_argument("--debug", action="store_true", help="log the JSON responses a rendered page loads")
    parser.add_argument("--no-browser", action="store_true", help="never use Chrome")
    parser.add_argument("--time-limit", type=float, default=None, metavar="SECONDS", help="per-company limit (default: the app's 3 minutes)")
    parser.add_argument("--parallel", type=int, default=1, choices=(1, 2, 3), help="companies at once (default 1)")
    parser.add_argument("--show", type=int, default=0, help="also print this many posting titles per company")
    parser.add_argument("--real-data", action="store_true", help="use the real app's data folder instead of a temporary one")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    scratch = None
    if not args.real_data:
        scratch = tempfile.mkdtemp(prefix="knowitall-smoke-")
        os.environ["KNOWITALL_HOME"] = scratch
        os.environ["KNOWITALL_LEGACY_DIR"] = str(Path(scratch) / "no-legacy-data")
    sys.path.insert(0, str(ROOT))

    from knowitall import paths
    paths.enter_data_dir()
    from knowitall.browser import BrowserPool
    from knowitall.service import Service

    service = Service()
    service.runner.debug = args.debug
    pool = None if args.no_browser else BrowserPool(size=min(args.parallel, 2))
    service.attach_browser(pool)
    options = {"concurrency": args.parallel, "history": False, "autosave": False}
    if args.time_limit:
        options["time_limit"] = args.time_limit

    started = time.time()
    finished = {}
    try:
        if not service.start(args.domains, options):
            print("nothing to scan", file=sys.stderr)
            return 2
        while not service.wait(0.2):
            for company in service.state()["companies"]:
                if company["state"] not in ("queued", "scanning"):
                    finished.setdefault(company["domain"], time.time() - started)
        for company in service.state()["companies"]:
            finished.setdefault(company["domain"], time.time() - started)
    except KeyboardInterrupt:
        service.stop()
        service.wait(10)
    finally:
        if pool:
            pool.close()

    print()
    print(f"{'domain':<22}{'outcome':<17}{'postings':>9}{'seconds':>9}   detail")
    for company in service.state()["companies"]:
        jobs = service.jobs_of(company["domain"])
        print(f"{company['domain']:<22}{company.get('outcome') or company['state']:<17}{len(jobs):>9}"
              f"{finished.get(company['domain'], 0):>9.1f}   {company.get('outcome_detail') or ''}")
        if company.get("careers_note"):
            print(f"{'':<57}{company['careers_note']}")
        if company.get("careers_url"):
            print(f"{'':<57}open: {company['careers_url']}")
        for job in jobs[:args.show]:
            print(f"{'':<57}- {job['title']}  ({job.get('location') or '-'})")
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
