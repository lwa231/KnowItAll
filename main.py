"""KnowItAll: find a company's job listings from its web address.

Examples:
    python main.py stripe.com
    python main.py https://www.figma.com ramp.com --max-jobs 500
"""
import argparse
import os
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Find job listings on a company's website.")
    parser.add_argument("urls", nargs="+", help="company web address(es), e.g. stripe.com")
    parser.add_argument("--no-cache", action="store_true", help="re-download everything instead of using cached pages")
    parser.add_argument("--headful", action="store_true", help="show the Chrome window when the browser fallback runs")
    parser.add_argument("--no-browser", action="store_true", help="never fall back to Chrome")
    parser.add_argument("--max-jobs", type=int, default=2000, help="cap per company for very large boards (default 2000)")
    parser.add_argument("--max-enrich", type=int, default=50,
                        help="job pages to open for details in the generic fallback (default 50)")
    parser.add_argument("--parallel", type=int, default=1, help="companies to scrape at the same time (default 1)")
    parser.add_argument("--show", type=int, default=10, help="jobs to print per company (default 10)")
    return parser.parse_args()


def print_summary(results, show):
    print()
    for result in results:
        if not result:
            continue
        print(f"== {result['company']} ({result['domain']}) ==")
        print(f"   careers page : {result['careers_pages'][0] if result['careers_pages'] else 'not found'}")
        print(f"   source       : {result.get('source_detail') or result.get('source') or '-'}")
        for note in result["notes"]:
            print(f"   note         : {note}")
        print(f"   jobs         : {len(result['jobs'])}", end="")
        print(f"  ->  {', '.join(result.get('files', []))}" if result.get("files") else "")
        for job in result["jobs"][:show]:
            details = " | ".join(str(v) for v in (job["location"], job["department"]) if v)
            remote = "  [remote]" if job["remote"] else ""
            print(f"     - {job['title']}" + (f"  ({details})" if details else "") + remote)
        if len(result["jobs"]) > show:
            print(f"     ... and {len(result['jobs']) - show} more")
        print()


def main():
    args = parse_args()
    # Keep output/ and cache/ next to this script no matter where it's launched from.
    os.chdir(Path(__file__).resolve().parent)
    # Windows consoles can't print every character in job titles; replace instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from knowitall.fetch import settings
    from knowitall.scraper import scrape_jobs

    settings.cache = "REFRESH" if args.no_cache else True
    settings.headless = not args.headful
    settings.use_browser = not args.no_browser
    settings.max_jobs = args.max_jobs
    settings.max_enrich = args.max_enrich

    results = scrape_jobs(args.urls, parallel=args.parallel)
    print_summary(results, args.show)
    return 0 if any(r and r["jobs"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
