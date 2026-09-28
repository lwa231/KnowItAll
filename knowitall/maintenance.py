"""Keeping disk and memory bounded: the page cache, the database, the parsed-page cache.

Defaults (chosen in the plan): page cache 7 days, 50 scans per company, closed postings 180 days. Nothing here
touches exports (the files people asked for) or postings that are still listed.
"""
import os
import time

from . import fetch, paths, store

CACHE_MAX_AGE_DAYS = 7


def folder_size(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _remove_files(folder, older_than=None):
    """Delete files (all of them, or those not modified since `older_than`), then any emptied folders."""
    removed = freed = 0
    for root, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
                if older_than is None or stat.st_mtime < older_than:
                    os.remove(path)
                    removed += 1
                    freed += stat.st_size
            except OSError:
                pass
        if root != str(folder):
            try:
                os.rmdir(root)                       # only succeeds when empty
            except OSError:
                pass
    return {"files": removed, "bytes": freed}


def prune_cache(folder=None, max_age_days=CACHE_MAX_AGE_DAYS, now=None):
    folder = folder or paths.CACHE_DIR
    if not os.path.isdir(folder):
        return {"files": 0, "bytes": 0}
    return _remove_files(folder, older_than=(now or time.time()) - max_age_days * 86400)


def clear_cache(folder=None):
    folder = folder or paths.CACHE_DIR
    fetch._SOUPS.clear()
    if not os.path.isdir(folder):
        return {"files": 0, "bytes": 0}
    return _remove_files(folder)


def usage():
    return {
        "cache_bytes": folder_size(paths.CACHE_DIR),
        "database_bytes": store.database_size(),
        "logs_bytes": folder_size(paths.LOG_DIR),
        "exports_bytes": folder_size(paths.EXPORTS_DIR),
        "parsed_pages": fetch._SOUPS.stats(),
    }


def run_startup_prune():
    """Everything that is safe to do while starting. Never raises: housekeeping must not stop the app."""
    summary = {}
    for name, action in (("cache", prune_cache), ("history", store.prune)):
        try:
            summary[name] = action()
        except Exception as error:
            fetch.log(f"housekeeping ({name}) failed: {type(error).__name__}: {error}")
            summary[name] = {"error": str(error)}
    return summary
