"""The application's one API. The HTTP server and the command line both call these methods, so anything
you can do in the window can be done from a terminal (and a new capability is added here once, then
exposed by each front end).

Nothing here is a module-level singleton: build a Service (it creates its own Runner) wherever it is needed.
"""
import json
import os
import subprocess
import sys

import threading

from . import compat, export, fetch, maintenance, paths, settings as settings_store, store
from .fetch import log
from .runner import Runner


def _human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


class Service:
    def __init__(self, runner=None, settings_path=None):
        self.runner = runner or Runner()
        self.pool = None                                   # the scraping browsers, when there are any
        self.settings_path = settings_path
        self._settings_lock = threading.Lock()

    # ---------- the scraping browser ----------
    def attach_browser(self, pool, headless=True):
        """Use `pool` (a BrowserPool) for pages that need JavaScript; None means plain HTTP only."""
        self.pool = pool
        self.runner.set_browser(pool.render if pool else None, use_browser=pool is not None, headless=headless)
        if pool:
            pool.resize(self.get_settings()["browser_workers"])

    # ---------- settings ----------
    def get_settings(self):
        return settings_store.load(self.settings_path)

    def update_settings(self, update):
        """Validate, merge, save, and apply what can take effect at once. Raises ValueError for bad input."""
        accepted = settings_store.validate(update)
        with self._settings_lock:
            merged = {**settings_store.load(self.settings_path), **accepted}
            settings_store.save(merged, self.settings_path)
        if self.pool and "browser_workers" in accepted:
            self.pool.resize(accepted["browser_workers"])
        return settings_store.clean(merged)

    # ---------- health ----------
    def system_info(self):
        """Everything the System view shows: browser state, which HTTP client is serving, disk use, locations."""
        pool = self.pool
        return {
            "running": self.runner.running,
            "browser": {
                "enabled": pool is not None,
                "available": pool is not None and pool.unavailable is None,
                "reason": pool.unavailable if pool else "browser scraping is switched off (--no-browser-scraping)",
                "workers": pool.size if pool else 0,
                "active": pool.active_drivers() if pool else 0,
            },
            "requests": fetch.stack_stats(),
            "usage": maintenance.usage(),
            "paths": {"data": str(paths.DATA_DIR), "exports": str(paths.EXPORTS_DIR), "database": str(store.DB_PATH),
                      "cache": str(paths.CACHE_DIR), "logs": str(paths.LOG_DIR)},
            "versions": {"botasaurus": compat.installed_version(), "tested_with": compat.PINNED_VERSION},
        }

    # ---------- running scans ----------
    def start(self, urls, options=None):
        return self.runner.start(urls, options)

    def queue(self, urls):
        return self.runner.queue(urls)

    def stop(self, domain=None):
        return self.runner.stop(domain)

    def wait(self, timeout=None):
        return self.runner.wait(timeout)

    def state(self, since=0):
        """Queue and company states plus every event after `since` (the cursor the UI polls with)."""
        events, cursor = self.runner.events_since(since)
        state = self.runner.snapshot()
        state.update(events=events, cursor=cursor)
        return state

    # ---------- reading results ----------
    def query_jobs(self, filters=None):
        return store.query_jobs(filters)

    def history(self, limit=200):
        return {"runs": store.history(limit)}

    def run_jobs(self, run_id):
        return {"jobs": store.run_jobs(run_id)}

    def jobs_of(self, domain):
        return self.runner.jobs_of(domain)

    # ---------- housekeeping ----------
    def maintenance_status(self):
        return {**maintenance.usage(), "running": self.runner.running}

    def clear_cache(self):
        return maintenance.clear_cache()

    def compact_database(self):
        """Shrink the database file. Refused while a scan is running (it needs the file to itself)."""
        if self.runner.running:
            return None
        return store.compact()

    def prune_in_background(self):
        """Startup housekeeping: old cache files, old scans, long-closed postings. Returns the thread."""
        def work():
            summary = maintenance.run_startup_prune()
            removed = sum(v.get("files", 0) + v.get("runs", 0) + v.get("postings", 0)
                          for v in summary.values() if isinstance(v, dict))
            if removed:
                log(f"housekeeping removed {removed} old item(s): {summary}")
        thread = threading.Thread(target=work, name="housekeeping", daemon=True)
        thread.start()
        return thread

    # ---------- exports ----------
    def list_exports(self):
        folder = paths.EXPORTS_DIR
        if not folder.exists():
            return []
        files = []
        for item in sorted(folder.glob("jobs_*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
            rows = None
            try:
                if item.suffix == ".json":
                    rows = len(json.loads(item.read_text(encoding="utf-8") or "[]"))
                elif item.suffix == ".csv":
                    rows = max(0, sum(1 for _ in item.open(encoding="utf-8-sig")) - 1)
            except Exception:
                rows = None
            files.append({"name": item.name, "rows": rows, "size": _human_size(item.stat().st_size),
                          "written": item.stat().st_mtime})
        return files

    def export_all(self):
        """Every job of every company in the queue, in one CSV."""
        rows = []
        for domain in list(self.runner.order):
            rows.extend(self.runner.jobs_of(domain))
        if not rows:
            return {"written": None, "rows": 0}
        return {"written": export.export_all(rows), "rows": len(rows)}

    def open_exports(self):
        paths.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        target = str(paths.EXPORTS_DIR.resolve())
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", target])
            elif os.name == "nt":
                os.startfile(target)                                   # noqa: S606 - Windows only
            else:
                subprocess.Popen(["xdg-open", target])
            return True
        except Exception:
            return False
