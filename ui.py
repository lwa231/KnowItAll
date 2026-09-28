"""KnowItAll desktop app.

Starts a local server and shows the interface in its own native window (pywebview: WKWebView on
macOS, Edge WebView2 on Windows). The window is never a Chrome window. Scraping pages that need
JavaScript uses a separate, hidden headless Chrome managed by knowitall.browser.BrowserPool.
Closing the window quits the app.

    python ui.py [--debug] [--no-browser-scraping] [--browser-workers N]
"""
import argparse
import signal
import sys
import threading
import time

from knowitall import paths

NO_WEBVIEW_HELP = """\
Could not open the KnowItAll window: {error}

  * Make sure pywebview is installed:  pip install pywebview
  * Windows: the Microsoft Edge WebView2 Runtime must be installed
    (https://developer.microsoft.com/microsoft-edge/webview2/). It ships with Windows 11.
  * Linux: install GTK or Qt bindings for pywebview.
"""

NO_CHROME_NOTICE = ("Google Chrome could not be started, so pages that need JavaScript will be skipped "
                    "(plain-HTML sites are unaffected). Install Chrome to scan those too.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="KnowItAll job scraper (desktop app).")
    parser.add_argument("--debug", action="store_true", help="enable the window's developer tools")
    parser.add_argument("--no-browser-scraping", action="store_true",
                        help="never start Chrome for scraping; HTTP fetching only")
    parser.add_argument("--browser-workers", type=int, default=None, choices=(1, 2, 3),
                        help="scraping Chrome instances that may render pages in parallel (default: the saved setting, 1)")
    return parser.parse_args(argv)


def _handle_interrupt(window):
    """Ctrl+C in the launching terminal should close the window, so the normal shutdown runs.

    Elsewhere a plain signal handler does that. On macOS pywebview routes SIGINT to NSApp.terminate, which
    exits the process without running Python's cleanup (a hidden Chrome would be orphaned); it installs that
    route just before its run loop starts, so ours is swapped in once the window is showing.

    PyObjC's signal module has to be imported here, on the main thread, before the window starts: imported for
    the first time from the helper thread below, interrupts stop reaching the handler at all (found by testing).
    """
    if sys.platform != "darwin":
        signal.signal(signal.SIGINT, lambda *_: window.destroy())
        return
    try:
        from PyObjCTools import MachSignals
        from objc import _machsignals
    except ImportError:
        return

    def take_over_sigint():
        for _ in range(20):                          # wait until pywebview's own route is in place
            if getattr(MachSignals.getsignal(signal.SIGINT), "__name__", "") == "machInterrupt":
                break
            time.sleep(0.25)
        # Swap only the handler in the table: pywebview already registered the signal with the main thread's run
        # loop, and registering it again from this helper thread would send interrupts to a loop that never runs.
        _machsignals._signalmapping[signal.SIGINT] = lambda signum: window.destroy()

    window.events.shown += take_over_sigint


def main(argv=None):
    args = parse_args(argv)

    # history.db, cache/ and logs/ live in the per-user data folder, whatever the launch directory.
    paths.enter_data_dir()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from knowitall import compat, server, shell
    from knowitall.browser import BrowserPool
    from knowitall.fetch import log
    from knowitall.service import Service

    compat.check_version(log)
    instance = shell.SingleInstance(paths.DATA_DIR / "app.lock")
    if not instance.acquire():
        log("KnowItAll is already running (one copy per user at a time); close it first.")
        return 2

    try:
        import webview
    except ImportError as error:
        print(NO_WEBVIEW_HELP.format(error=error), file=sys.stderr)
        instance.release()
        return 1

    service = Service()
    runner = service.runner
    httpd, url = server.serve(service)
    log(f"server ready at {url}")
    service.prune_in_background()

    pool = None
    if not args.no_browser_scraping:
        pool = BrowserPool(
            size=args.browser_workers or service.get_settings()["browser_workers"],
            on_unavailable=lambda reason: runner.notice("no_chrome", "warning", NO_CHROME_NOTICE),
        )
    service.attach_browser(pool)
    if pool and args.browser_workers:
        pool.resize(args.browser_workers)                    # an explicit flag wins over the saved setting

    stopping = threading.Event()
    window = None

    def shutdown():
        if stopping.is_set():
            return
        stopping.set()
        log("shutting down")
        service.stop()
        try:
            server.shutdown(httpd)
        except Exception:
            pass
        if pool:
            pool.close()
        instance.release()

    # ---------- window ----------
    state_file = paths.DATA_DIR / "window.json"
    state = shell.load_window_state(state_file)
    try:
        screens = [(s.x, s.y, s.width, s.height) for s in webview.screens]
    except Exception:
        screens = []
    options = {"width": state["width"], "height": state["height"], "min_size": shell.MIN_SIZE}
    if shell.fits_on_screen(state, screens):
        options.update(x=state["x"], y=state["y"])

    def on_closing():
        if shell.confirm_close_needed(runner.running):
            return bool(window.create_confirmation_dialog(
                "A scan is still running",
                "Quit KnowItAll now? Jobs found so far are kept."))
        return True

    try:
        window = webview.create_window("KnowItAll", url, **options)
        window.events.resized += lambda width, height: state.update(width=width, height=height)
        window.events.moved += lambda x, y: state.update(x=x, y=y)
        window.events.closing += on_closing
        window.events.closed += lambda: shell.save_window_state(state_file, state)
        server.shutdown_hook = lambda: window.destroy()
        _handle_interrupt(window)

        log("KnowItAll is running. Close the window to quit.")
        webview.start(debug=args.debug, gui=shell.webview_backend())
    except Exception as error:
        log(f"could not open the window: {type(error).__name__}: {error}")
        print(NO_WEBVIEW_HELP.format(error=error), file=sys.stderr)
        return 1
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
