"""The application's one API. The HTTP server and the command line both call these methods, so anything
you can do in the window can be done from a terminal (and a new capability is added here once, then
exposed by each front end).

Nothing here is a module-level singleton: build a Service (it creates its own Runner) wherever it is needed.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import __version__, compat, export, fetch, geo, maintenance, paths, settings as settings_store, store
from .fetch import log
from .runner import Runner, clean_options


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
        self.runner.parallel = self.get_settings()["concurrency"]
        self.runner.exporter = self._export_company       # per-company autosave honours the filters
        self.session_id = None                            # one per app launch (see begin_session)

    # ---------- sessions ----------
    def begin_session(self):
        """Start this launch's session. History and the feed show only it; runs a killed app left 'running' are
        marked interrupted. Needs the database to exist (store.init)."""
        self.session_id = store.start_session(__version__)
        self.runner.session_id = self.session_id
        interrupted = store.mark_interrupted(self.session_id)
        if interrupted:
            log(f"marked {interrupted} unfinished scan(s) from an earlier session as interrupted")
        return self.session_id

    def end_session(self):
        if self.session_id:
            try:
                store.end_session(self.session_id)
            except Exception:
                pass

    # ---------- the scraping browser ----------
    def attach_browser(self, pool, headless=True):
        """Use `pool` (a BrowserPool) for pages that need JavaScript; None means plain HTTP only."""
        self.pool = pool
        self.runner.set_browser(pool.render if pool else None, use_browser=pool is not None, headless=headless)
        self._resize_browsers()

    def _resize_browsers(self):
        """One scraping browser per company scanning at once, but never more than two (each is a whole Chrome)."""
        if not self.pool:
            return
        settings = self.get_settings()
        self.pool.resize(min(self.runner.parallel, 2) if settings["browser_workers_auto"] else settings["browser_workers"])

    # ---------- settings ----------
    def get_settings(self):
        return settings_store.load(self.settings_path)

    def update_settings(self, update):
        """Validate, merge, save, and apply what can take effect at once. Raises ValueError for bad input."""
        accepted = settings_store.validate(update)
        if "browser_workers" in accepted and "browser_workers_auto" not in accepted:
            accepted["browser_workers_auto"] = False           # choosing a number means "not automatic"
        with self._settings_lock:
            merged = {**settings_store.load(self.settings_path), **accepted}
            settings_store.save(merged, self.settings_path)
        if "concurrency" in accepted:
            self.runner.set_parallel(accepted["concurrency"])
        if {"browser_workers", "browser_workers_auto", "concurrency"} & set(accepted):
            self._resize_browsers()
        return settings_store.clean(merged)

    # ---------- the saved filter profile ----------
    def get_filters(self):
        return self.get_settings()["filters"]

    def set_filters(self, raw):
        """Save the chip filters (they persist across launches and hide non-matching postings everywhere).
        Validated like a query, so a bad value is refused; returns what was saved. Raises ValueError."""
        if not isinstance(raw, dict):
            raise ValueError("filters must be a JSON object")
        subset = {k: raw[k] for k in (*settings_store.PERSISTED_LISTS, "posted_within_days") if raw.get(k) not in (None, [], "")}
        checked = store.normalize_filters(subset)
        profile = {k: checked[k] for k in settings_store.PERSISTED_LISTS if checked[k]}
        if checked["posted_within_days"]:
            profile["posted_within_days"] = checked["posted_within_days"]
        with self._settings_lock:
            merged = {**settings_store.load(self.settings_path), "filters": profile}
            settings_store.save(merged, self.settings_path)
        return profile

    def countries(self):
        return {"countries": geo.all_countries()}

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
    def run_options(self):
        """What a scan runs with: the saved settings. The server decides; a client cannot send its own caps."""
        s = self.get_settings()
        return {"max_jobs": s["max_jobs"], "max_enrich": s["max_enrich"], "concurrency": s["concurrency"],
                "cache_reuse": s["cache_reuse"], "autosave": s["autosave"], "time_limit_min": s["time_limit_min"]}

    def scan(self, urls):
        """Enter pressed on an address. Auto: it scans as soon as a slot is free (else it waits its turn). Manual: it is
        added to the list and waits for start_ready(). Returns the runner's {added, ignored, invalid} plus the mode."""
        mode = self.get_settings()["parallel_mode"]
        result = self.runner.submit(urls, start=mode == "auto", options=self.run_options())
        self._resize_browsers()
        return {**result, "mode": mode}

    def start_ready(self):
        """Manual mode's Start: scan everything that was added and is waiting. Returns how many started."""
        return self.runner.start_ready(self.run_options())

    def set_parallel(self, n):
        """The "At once" control: save it and apply it to the running scans now."""
        self.update_settings({"concurrency": n})
        return {"parallel": self.runner.parallel}

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
        """Postings matching the filters. session=current (the default, once a session has begun) looks only at what
        this launch scanned; session=all also sees earlier ones (they stay in the database for new/gone/closed)."""
        raw = dict(filters or {})
        mode = raw.pop("session", None)
        mode = (mode[0] if isinstance(mode, (list, tuple)) and mode else mode) or "current"
        if mode not in ("current", "all"):
            raise ValueError("session must be current or all")
        raw.pop("session_id", None)                        # only the service decides which session
        if mode == "current" and self.session_id:
            raw["session_id"] = self.session_id
        return store.query_jobs(raw)

    def history(self, limit=200, session="current", filters=None):
        """This session's scans (or all of them), newest first, each with how it ended and - when filters are
        given - how many of its postings match."""
        if session not in ("current", "all"):
            raise ValueError("session must be current or all")
        runs = store.history(limit, self.session_id if session == "current" else None)
        if filters:
            counts = store.run_match_counts(filters, [r["id"] for r in runs])
            for run in runs:
                run["matches"] = counts.get(run["id"], 0)
        return {"runs": runs}

    def run_jobs(self, run_id):
        return {"jobs": store.run_jobs(run_id)}

    def jobs_of(self, domain):
        return self.runner.jobs_of(domain)

    # ---------- housekeeping ----------
    def maintenance_status(self):
        return {**maintenance.usage(), "running": self.runner.running}

    def clear_cache(self):
        """Delete saved pages (the next scan of each company downloads fresh). Refused (None) while a scan runs:
        a worker could be about to read a file that is deleted under it (audit L10)."""
        if self.runner.running:
            return None
        return maintenance.clear_cache()

    def compact_database(self):
        """Shrink the database file. Refused while a scan is running (it needs the file to itself)."""
        if self.runner.running:
            return None
        return store.compact()

    def prune_in_background(self):
        """Startup housekeeping: old cache files, old scans, long-closed postings. Returns the thread."""
        def work():
            summary = maintenance.run_startup_prune(self.get_settings()["cache_reuse"])
            removed = sum(v.get("files", 0) + v.get("runs", 0) + v.get("postings", 0)
                          for v in summary.values() if isinstance(v, dict))
            if removed:
                log(f"housekeeping removed {removed} old item(s): {summary}")
        thread = threading.Thread(target=work, name="housekeeping", daemon=True)
        thread.start()
        return thread

    # ---------- exports ----------
    def _notice_renamed(self, wanted, used):
        self.runner.notice("export_locked", "warning",
                           f"{Path(wanted).name} is open in another program, so the new export was saved as {Path(used).name}.")

    def filter_summary(self, filters):
        """'Remote · United States · last 7 days' for the filters that narrow results, or '' when none do."""
        f = store.normalize_filters(filters)
        labels = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "unknown": "not stated", "full_time": "Full-time",
                  "part_time": "Part-time", "contract": "Contract", "intern": "Intern", "other": "Other"}
        parts = []
        for key in ("workplace", "employment_type"):
            chosen = [labels.get(v, v) for v in f[key]]
            if [v for v in f[key] if v != "unknown"]:
                parts.append(", ".join(chosen))
        regions = [*f["region_group"], *[geo.country_name(c) or c for c in f["country"] if c != "unknown"]]
        if regions:
            parts.append(", ".join(regions + (["no location"] if "unknown" in f["country"] else [])))
        for key in ("department", "source"):
            if f[key]:
                parts.append(", ".join(f[key]))
        if f["posted_within_days"]:
            parts.append({1: "last 24 hours"}.get(f["posted_within_days"], f"last {f['posted_within_days']} days"))
        if f["new_only"]:
            parts.append("new only")
        if f["q"]:
            parts.append(f"\u201c{f['q']}\u201d")
        return " \u00b7 ".join(parts)

    def _export_filters(self, payload=None):
        """The filters an export must obey, or None (everything): the saved profile, or the ones the window sends."""
        if self.get_settings()["export_scope"] != "matching":
            return None
        raw = payload if isinstance(payload, dict) else self.get_filters()
        raw = {k: v for k, v in (raw or {}).items() if v not in (None, [], "", False)}
        raw.pop("status", None)
        try:
            return raw if self.filter_summary(raw) else None
        except ValueError:
            return None

    def _matching_rows(self, raw, **scope):
        """Every posting matching `raw` within `scope` (run_id, session_id...), paged through the same query the feed uses."""
        rows, offset = [], 0
        while True:
            page = store.query_jobs({**raw, **scope, "limit": store.MAX_LIMIT, "offset": offset, "facets": False})
            rows.extend(page["jobs"])
            offset += store.MAX_LIMIT
            if offset >= page["total"] or not page["jobs"]:
                return rows, page["total"]

    def _export_company(self, domain, run_id, jobs):
        """A company's autosave files: its postings that match the filters (named ..._filtered), or all of them."""
        filters = self._export_filters() if run_id else None
        if not filters:
            return export.export_jobs(domain, jobs, on_renamed=self._notice_renamed)
        matching, _ = self._matching_rows(filters, run_id=run_id)
        return export.export_jobs(domain, matching, self.filter_summary(filters), total=len(jobs), on_renamed=self._notice_renamed)

    def list_exports(self):
        folder = paths.EXPORTS_DIR
        if not folder.exists():
            return []
        began = None
        if self.session_id:
            started = store.session_started(self.session_id)
            began = datetime.fromisoformat(started).timestamp() if started else None
        files = []
        for pattern in ("jobs_*.*", "knowitall_session_*.*"):
            for item in folder.glob(pattern):
                if item.suffix not in (".json", ".csv"):
                    continue
                rows = None
                try:
                    if item.suffix == ".json":
                        data = json.loads(item.read_text(encoding="utf-8") or "[]")
                        rows = len(data["jobs"] if isinstance(data, dict) else data)
                    else:
                        rows = max(0, sum(1 for _ in item.open(encoding="utf-8-sig")) - 1)
                except Exception:
                    rows = None
                stat = item.stat()
                files.append({"name": item.name, "rows": rows, "size": _human_size(stat.st_size), "written": stat.st_mtime,
                              "this_session": bool(began and stat.st_mtime >= began)})
        return sorted(files, key=lambda f: (not f["this_session"], -f["written"]))

    def export_session(self, filters=None):
        """'Export this session': one CSV of this launch's postings - the ones matching the filters, unless Settings says everything."""
        active = self._export_filters(filters)
        raw = active or {}
        rows, total = self._matching_rows(raw, session_id=self.session_id) if self.session_id else ([], 0)
        if not rows:
            return {"written": None, "rows": 0, "filtered": bool(active)}
        everything = self._matching_rows({}, session_id=self.session_id)[1] if active else total
        path = export.export_session(rows, self.filter_summary(active) if active else None, total=everything,
                                     on_renamed=self._notice_renamed)
        return {"written": path, "rows": len(rows), "filtered": bool(active)}

    def export_all(self):
        """Every job of every company in the queue, in one CSV (the command line's shape)."""
        rows = []
        for domain in list(self.runner.order):
            rows.extend(self.runner.jobs_of(domain))
        if not rows:
            return {"written": None, "rows": 0}
        return {"written": export.export_all(rows, on_renamed=self._notice_renamed), "rows": len(rows)}

    def _open_folder(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        target = str(folder.resolve())
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

    def open_exports(self):
        return self._open_folder(paths.EXPORTS_DIR)

    # ---------- backups ----------
    BACKUPS_KEPT = 10

    def create_backup(self, kind="manual"):
        """Copy the database (SQLite's backup API, so it is consistent even mid-scan) and settings.json into the backups
        folder. Returns {name, kind, bytes, created}. Manual backups are never deleted automatically."""
        if kind not in ("auto", "manual"):
            raise ValueError("kind must be auto or manual")
        paths.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        stem, n = f"knowitall-{stamp}-{kind}", 0
        while (paths.BACKUPS_DIR / f"{stem}.db").exists():
            n += 1
            stem = f"knowitall-{stamp}-{n}-{kind}"
        target = store.backup_to(paths.BACKUPS_DIR / f"{stem}.db")
        settings_file = Path(self.settings_path or paths.SETTINGS_PATH)
        if settings_file.exists():
            shutil.copy2(settings_file, paths.BACKUPS_DIR / f"{stem}-settings.json")
        if kind == "auto":
            self._rotate_backups()
        return self._backup_info(target)

    def _backup_info(self, path):
        stat = path.stat()
        return {"name": path.name, "kind": "auto" if path.stem.endswith("-auto") else "manual", "bytes": stat.st_size,
                "created": stat.st_mtime}

    def list_backups(self):
        folder = paths.BACKUPS_DIR
        if not folder.exists():
            return []
        return sorted((self._backup_info(p) for p in folder.glob("knowitall-*.db")), key=lambda b: -b["created"])

    def _rotate_backups(self):
        autos = sorted((b for b in self.list_backups() if b["kind"] == "auto"), key=lambda b: -b["created"])
        for old in autos[self.BACKUPS_KEPT:]:
            for name in (old["name"], old["name"][:-3] + "-settings.json"):
                try:
                    (paths.BACKUPS_DIR / name).unlink()
                except OSError:
                    pass

    def auto_backup(self):
        """At launch, before the database is migrated: back up only if it holds something and has changed since the last
        automatic backup (so ten quick relaunches cannot push every older backup out). Returns the backup or None."""
        if not self.get_settings()["auto_backup"]:
            return None
        fingerprint = store.data_fingerprint()
        marker = paths.BACKUPS_DIR / ".last_auto.json"
        try:
            last = json.loads(marker.read_text(encoding="utf-8")).get("fingerprint")
        except (OSError, ValueError):
            last = None
        if fingerprint is None or fingerprint == last:
            return None
        backup = self.create_backup("auto")
        marker.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
        return backup

    def open_backups(self):
        return self._open_folder(paths.BACKUPS_DIR)
