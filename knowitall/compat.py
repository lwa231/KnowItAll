"""Everything KnowItAll relies on in botasaurus that is not its documented public API, in one place.

botasaurus is pinned (requirements.txt) because of these. If it is ever upgraded, this file is the list of
things to re-check, and tests/test_botasaurus_compat.py pins each of them.

  1. botasaurus_requests.reqs.retry_on_network_error is replaced so a dead host fails at once instead of
     being retried every 20 seconds forever (it assumes "no such host" means the internet is down).
  2. Cache.set_cache_directory / the cwd-relative cache/ folder (paths.enter_data_dir sets the cwd).
  3. Driver.get(url, timeout=...), .current_url, .page_html, .run_js(), .close() (browser.BrowserPool).
"""
import importlib.metadata

PINNED_VERSION = "4.0.97"


def apply():
    """Install the one patch. Idempotent."""
    from botasaurus_requests import reqs
    reqs.retry_on_network_error = lambda func: func()


def installed_version():
    try:
        return importlib.metadata.version("botasaurus")
    except importlib.metadata.PackageNotFoundError:
        return None


def check_version(log):
    """Warn (once, at startup) when the installed botasaurus is not the version this code was tested with."""
    version = installed_version()
    if version != PINNED_VERSION:
        log(f"botasaurus {version} is installed but KnowItAll is tested with {PINNED_VERSION}; "
            "scraping may misbehave (see knowitall/compat.py for what it depends on)")
        return False
    return True
