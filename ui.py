"""KnowItAll desktop UI.

Starts a local server, opens one Chrome window on it, and uses that same browser for
scraping pages that need JavaScript. Closing the window shuts everything down.

    python ui.py [--headless] [--no-browser-scraping]
"""
import argparse
import os
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="KnowItAll job scraper (desktop UI).")
    parser.add_argument("--headless", action="store_true", help="run Chrome without a window (testing only)")
    parser.add_argument("--no-browser-scraping", action="store_true",
                        help="never open scraping tabs; HTTP fetching only")
    args = parser.parse_args()

    # Keep output/, cache/ and history.db next to this script wherever it is launched from.
    os.chdir(Path(__file__).resolve().parent)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from knowitall import server
    from knowitall.browser import SharedBrowser
    from knowitall.fetch import settings, log

    httpd, url = server.serve()
    log(f"server ready at {url}")

    browser = SharedBrowser(headless=args.headless)
    stopping = threading.Event()

    def shutdown():
        if stopping.is_set():
            return
        stopping.set()
        log("shutting down")
        try:
            httpd.shutdown()
        except Exception:
            pass
        browser.close()

    server.shutdown_hook = shutdown

    try:
        browser.start(url)
    except Exception as error:
        log(f"could not start Chrome: {type(error).__name__}: {error}")
        log("Is Google Chrome installed? The UI needs it; the command line scraper does not.")
        return 1

    # The one Chrome does double duty: it shows the UI and renders pages the scraper needs.
    settings.use_browser = not args.no_browser_scraping
    if settings.use_browser:
        settings.renderer = browser.render

    log("KnowItAll is running. Close the Chrome window (or press Ctrl+C) to quit.")
    try:
        while not stopping.is_set():
            time.sleep(1.0)
            if not browser.is_alive():          # user closed the window
                break
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
