"""Run manager: the queue, concurrency, Stop, and the event stream the UI polls.

Nothing here is global: a Runner owns its queue, its cancel tokens and (through set_browser) the
renderer its scans may use. Every scan gets its own ScanContext built from a RunConfig, so two
Runners - or two consecutive Starts with different options - never influence each other.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from . import store
from .context import CancelToken, RunConfig, ScanContext
from .discovery import parse_site
from .export import export_jobs
from .fetch import log_exception
from .scraper import find_jobs

MAX_EVENTS, KEEP_EVENTS = 20000, 10000
DEFAULT_MAX_JOBS, DEFAULT_MAX_ENRICH = 2000, 50


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self._wake = threading.Condition(self.lock)         # notified whenever an event is added
        self.companies = {}        # domain -> company state shown in the UI (only mutated under self.lock)
        self.order = []            # queue order
        self.events = []           # the UI polls by cursor; trimmed from the front to bound memory
        self._next_id = 0          # monotonic: ids are never reused, even after a trim
        self.notices = {}          # code -> {level, text}: persistent, non-blocking messages for the UI
        self.running = False
        self.cancel = CancelToken()        # Stop for the current run; a fresh one is made by every start()
        self.tokens = {}           # domain -> that company's own token (child of self.cancel)
        self.pool = None
        self.renderer = None       # callable(url, should_cancel) -> page dict, e.g. BrowserPool.render
        self.use_browser = True
        self.headless = True
        self.autosave = True
        self.history = True
        self._jobs = {}            # domain -> jobs kept in memory when history is off
        self._idle = threading.Event()
        self._idle.set()

    def set_browser(self, renderer=None, use_browser=True, headless=True):
        """The renderer scans may use for JavaScript pages (None: plain HTTP, or the standalone Chrome)."""
        self.renderer, self.use_browser, self.headless = renderer, use_browser, headless

    # ---------- events ----------
    def emit(self, kind, **payload):
        with self.lock:
            self.events.append({"i": self._next_id, "kind": kind, **payload})
            self._next_id += 1
            if len(self.events) > MAX_EVENTS:             # keep memory bounded on long sessions
                self.events = self.events[-KEEP_EVENTS:]
            self._wake.notify_all()

    def log(self, message):
        self.emit("log", text=str(message))

    def notice(self, code, level, text):
        """A persistent message (e.g. Chrome missing) that the UI can show without blocking anything."""
        with self.lock:
            self.notices[code] = {"code": code, "level": level, "text": text}
        self.emit("notice", code=code, level=level, text=text)
        self.log(text)

    def events_since(self, cursor):
        with self.lock:
            return [e for e in self.events if e["i"] >= cursor], self._next_id

    def wait_for_events(self, cursor, timeout=1.0):
        """Block until there is an event at or after `cursor` (or the timeout passes); returns events_since()."""
        with self._wake:
            if self._next_id <= cursor:
                self._wake.wait(timeout)
            return [e for e in self.events if e["i"] >= cursor], self._next_id

    # ---------- state ----------
    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "notices": list(self.notices.values()),
                "companies": [dict(self.companies[d], notes=list(self.companies[d]["notes"])) for d in self.order],
            }

    def _update(self, domain, **fields):
        with self.lock:
            self.companies[domain].update(fields)

    def _bump(self, domain, **deltas):
        with self.lock:
            state = self.companies[domain]
            for key, delta in deltas.items():
                state[key] = state.get(key, 0) + delta

    def _read(self, domain, key, default=None):
        with self.lock:
            return self.companies[domain].get(key, default)

    def jobs_of(self, domain):
        """The jobs found for a company in the current session (from history, or memory when history is off)."""
        with self.lock:
            run_id = self.companies.get(domain, {}).get("run_id")
            kept = list(self._jobs.get(domain, []))
        return store.run_jobs(run_id) if run_id else kept

    # ---------- control ----------
    def queue(self, urls):
        added = []
        for raw in urls:
            try:
                site = parse_site(raw)
            except ValueError:
                self.log(f"skipping '{raw}': not a valid web address")
                continue
            with self.lock:
                if site.domain in self.companies:
                    continue
                self.companies[site.domain] = {
                    "domain": site.domain, "company": site.name, "input": site.input_url, "state": "queued",
                    "jobs_count": 0, "new_count": 0, "missing_count": 0, "closed_count": 0, "source": None,
                    "careers_page": None, "run_id": None, "notes": [],
                }
                self.order.append(site.domain)
            added.append(site.domain)
        self.emit("companies")
        return added

    def start(self, urls, options=None):
        options = options or {}
        with self.lock:
            if self.running:
                return False
            self.running = True                             # claimed: a second start() now returns False
        try:
            use_browser = bool(options.get("use_browser", self.use_browser))
            config = RunConfig(
                cache="REFRESH" if options.get("fresh") else True,
                max_jobs=int(options.get("max_jobs") or DEFAULT_MAX_JOBS),
                max_enrich=int(options.get("max_enrich") or DEFAULT_MAX_ENRICH),
                use_browser=use_browser, headless=self.headless,
                renderer=self.renderer if use_browser else None,
            )
            concurrency = max(1, min(4, int(options.get("concurrency") or 3)))
            self.autosave = options.get("autosave", True)
            self.history = options.get("history", True)

            self.queue(urls)
            with self.lock:
                pending = [d for d in self.order if self.companies[d]["state"] in ("queued", "stopped", "failed")]
                if not pending:
                    pending = list(self.order)              # everything finished: pressing Start means "scan again"
                for domain in pending:
                    self.companies[domain].update(state="queued", jobs_count=0, new_count=0, missing_count=0, closed_count=0)
                    self._jobs.pop(domain, None)
            if not pending:
                raise LookupError("nothing to scan")
            self.cancel = CancelToken()
            self.tokens = {domain: self.cancel.child() for domain in pending}
        except Exception as error:
            with self.lock:
                self.running = False
            if not isinstance(error, LookupError):
                raise
            return False

        self._idle.clear()
        self.emit("status", running=True)
        self.pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="scrape")
        threading.Thread(target=self._drive, args=(pending, config), daemon=True).start()
        return True

    def stop(self, domain=None):
        """Stop everything, or just one company (whose partial results are kept)."""
        if domain:
            token = self.tokens.get(domain)
            if token is None:
                return False
            token.cancel()
            self.log(f"stopping {domain} - finishing the current step")
            return True
        self.cancel.cancel()
        self.log("stopping - finishing the current step")
        return True

    def wait(self, timeout=None):
        """Block until the current run has finished (True), or the timeout passes (False)."""
        return self._idle.wait(timeout)

    def _drive(self, pending, config):
        try:
            futures = [self.pool.submit(self._scrape_one, d, config) for d in pending]
            for f in futures:
                try:
                    f.result()
                except Exception:
                    log_exception("scrape worker crashed")
        finally:
            with self.lock:
                self.running = False
            self.pool.shutdown(wait=False)
            self.emit("status", running=False)
            self._idle.set()

    # ---------- one company ----------
    def _scrape_one(self, domain, config):
        token = self.tokens[domain]
        if token.is_set():
            self._update(domain, state="stopped")
            self.emit("companies")
            return

        self._update(domain, state="scanning", jobs_count=0, new_count=0, missing_count=0, closed_count=0)
        company = self._read(domain, "company") or domain
        run_id = store.start_run(domain, company) if self.history else None
        self._update(domain, run_id=run_id)
        self.emit("reset", domain=domain)      # tell the UI to drop this company's old rows
        self.emit("companies")

        collected = []

        def on_jobs(batch):
            batch = [dict(j) for j in batch]
            if self.history:
                new_count = store.mark_new(batch, domain)
                store.save_jobs(run_id, batch)
            else:
                new_count = 0
                for job in batch:
                    job["is_new"] = False
            collected.extend(batch)
            self._update(domain, jobs_count=len(collected))
            self._bump(domain, new_count=new_count)
            # No rows in the event: clients ask /api/jobs for what they show, so a big board never floods the stream.
            self.emit("jobs", domain=domain, added=len(batch), total=len(collected))

        ctx = ScanContext(config, token, log_sink=self.log)
        try:
            result = find_jobs(ctx, self._read(domain, "input") or domain, on_jobs=on_jobs)
        except Exception as error:
            log_exception(f"{domain}: scrape failed")
            self._update(domain, state="failed", notes=[f"{type(error).__name__}: {error}"])
            if run_id:
                store.finish_run(run_id, "failed", None, len(collected), self._read(domain, "new_count", 0))
            self.emit("companies")
            return

        stopped = bool(result.get("stopped")) or token.is_set()
        state = "stopped" if stopped else "done"
        self._update(
            domain, company=result.get("company") or company, source=result.get("source"),
            source_detail=result.get("source_detail"), careers_page=(result.get("careers_pages") or [None])[0],
            notes=result.get("notes") or [], jobs_count=len(collected), state=state)
        if not self.history:
            with self.lock:
                self._jobs[domain] = collected
        if run_id:
            complete = state == "done" and not result.get("truncated")
            outcome = store.finish_run(run_id, state, result.get("source"), len(collected),
                                       self._read(domain, "new_count", 0), complete=complete)
            if outcome["closed"] or outcome["missing"]:
                self._update(domain, missing_count=outcome["missing"], closed_count=outcome["closed"])
                self.log(f"{domain}: {outcome['closed']} posting(s) closed, {outcome['missing']} no longer listed")

        if collected and self.autosave:
            self._write_files(domain, collected)
        self.emit("companies")

    def _write_files(self, domain, jobs):
        try:
            export_jobs(domain, jobs)
        except Exception as error:
            self.log(f"could not write output for {domain}: {error}")
