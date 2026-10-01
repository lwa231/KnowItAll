"""Run manager: the queue, the scan slots, Stop, and the event stream the UI follows.

Nothing here is global: a Runner owns its queue, its cancel tokens and (through set_browser) the
renderer its scans may use. Every scan gets its own ScanContext built from a RunConfig, so two
Runners - or two consecutive Starts with different options - never influence each other.
"""
import threading
import time
from collections import deque

from . import outcomes, store
from .context import TIME_LIMIT, CancelToken, RunConfig, ScanContext
from .discovery import parse_site
from .export import export_jobs
from .fetch import clear_dns_cache, log_exception
from .scraper import find_jobs

MAX_EVENTS, KEEP_EVENTS = 20000, 10000
DEFAULT_MAX_JOBS, DEFAULT_MAX_ENRICH = 2000, 50
DEFAULT_TIME_LIMIT_MIN = 3            # one company never runs longer than this; Settings offers 1 / 3 / 5
MAX_PARALLEL = 3

# Company states: ready (added, waiting for Start: Manual mode), waiting (wants a slot, none free), scanning, then
# done / stopped / failed. "running" means anything is scanning or waiting.
ACTIVE_STATES = ("scanning", "waiting")


def clean_options(options):
    """Validated run options. Raises ValueError naming the problem, so a bad request is refused instead of crashing."""
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise ValueError("options must be a JSON object")
    ranges = {"max_jobs": (1, 100_000), "max_enrich": (0, 500), "concurrency": (1, MAX_PARALLEL)}
    clean, problems = {}, []
    for key, (low, high) in ranges.items():
        if options.get(key) in (None, ""):
            continue
        value = options[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            problems.append(f"{key} must be a whole number from {low} to {high}")
        else:
            clean[key] = value
    if options.get("time_limit_min") not in (None, ""):
        if options["time_limit_min"] in (1, 3, 5) and not isinstance(options["time_limit_min"], bool):
            clean["time_limit_min"] = options["time_limit_min"]
        else:
            problems.append("time_limit_min must be 1, 3 or 5")
    if options.get("time_limit") not in (None, ""):
        value = options["time_limit"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 3600:
            problems.append("time_limit must be a number of seconds up to 3600")
        else:
            clean["time_limit"] = float(value)
    if options.get("cache_reuse") not in (None, ""):
        if options["cache_reuse"] in ("off", "1h", "12h"):
            clean["cache_reuse"] = options["cache_reuse"]
        else:
            problems.append("cache_reuse must be off, 1h or 12h")
    for key in ("fresh", "autosave", "history", "use_browser"):
        if key in options:
            if isinstance(options[key], bool):
                clean[key] = options[key]
            else:
                problems.append(f"{key} must be true or false")
    if problems:
        raise ValueError("; ".join(problems))
    return clean


class _Scan:
    """One company's scan in progress. `dead` is claimed, under the lock, by whoever finalizes it first (the worker
    finishing, Stop, or the time limit), so a scan is recorded exactly once and what a worker produces after that is
    discarded."""

    def __init__(self, config):
        self.config = config
        self.token = CancelToken()
        self.lock = threading.Lock()
        self.dead = False
        self.run_id = None
        self.timer = None


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self._wake = threading.Condition(self.lock)         # notified whenever an event is added
        self.companies = {}        # domain -> company state shown in the UI (only mutated under self.lock)
        self.order = []            # queue order
        self.events = []           # the UI polls by cursor; trimmed from the front to bound memory
        self._next_id = 0          # monotonic: ids are never reused, even after a trim
        self.session_id = None     # the launch this runner belongs to (History and the feed show only it)
        self.exporter = None       # callable(domain, run_id, jobs) that writes a company's files; None = plain export
        self.debug = False         # log the JSON a rendered page loads (to build per-site adapters)
        self.notices = {}          # code -> {level, text}: persistent, non-blocking messages for the UI
        self.running = False
        self.parallel = MAX_PARALLEL       # how many companies may scan at once (1-3); changes apply at once
        self.scans = {}            # domain -> its _Scan
        self._waiting = deque()    # domains that want a slot, in order
        self._active = set()       # domains holding a slot
        self.renderer = None       # callable(url, should_cancel) -> page dict, e.g. BrowserPool.render
        self.use_browser = True
        self.headless = True
        self.autosave = True
        self.history = True
        self._options = {}         # the options of the latest submit: what a later Start runs with
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
                "parallel": self.parallel,
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
    @staticmethod
    def _new_company(site):
        return {
            "domain": site.domain, "company": site.name, "input": site.input_url, "state": "ready",
            "jobs_count": 0, "new_count": 0, "missing_count": 0, "closed_count": 0, "source": None,
            "careers_page": None, "run_id": None, "notes": [],
            # how the last scan ended (outcomes.py), what it is doing right now, and when it started
            "outcome": None, "outcome_detail": None, "outcome_hint": None, "outcome_short": None,
            "careers_url": None, "careers_note": None, "phase": None, "started_at": None, "waiting_ahead": None,
        }

    def _config_from(self, options):
        use_browser = bool(options.get("use_browser", self.use_browser))
        return RunConfig(
            cache="REFRESH" if options.get("fresh") or options.get("cache_reuse") == "off" else True,
            reuse="1h" if options.get("cache_reuse") == "1h" else "12h",
            max_jobs=int(options.get("max_jobs") or DEFAULT_MAX_JOBS),
            max_enrich=int(options.get("max_enrich") or DEFAULT_MAX_ENRICH),
            use_browser=use_browser, headless=self.headless,
            renderer=self.renderer if use_browser else None,
            debug_payloads=self.debug,
            time_limit=float(options.get("time_limit") or (options.get("time_limit_min") or DEFAULT_TIME_LIMIT_MIN) * 60),
        )

    def set_parallel(self, n):
        """How many companies may scan at once. Raising it starts waiting companies now; lowering it lets the running
        ones finish and only holds back new starts."""
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PARALLEL:
            raise ValueError(f"parallel must be a whole number from 1 to {MAX_PARALLEL}")
        with self.lock:
            self.parallel = n
        self._fill_slots()
        return n

    def queue(self, urls):
        """Add companies without starting them (state "ready"). Returns the domains that were new."""
        return self.submit(urls, start=False)["added"]

    def submit(self, urls, start=True, options=None):
        """Add companies; with start=True they scan as slots free up (waiting in order), otherwise they become "ready"
        until start_ready(). A company already scanning or waiting is ignored; a finished one is scanned again.
        Returns {"added": [...], "ignored": [...], "invalid": [...]}."""
        options = clean_options(options)
        added, ignored, invalid = [], [], []
        config = self._config_from(options)
        self.autosave = options.get("autosave", self.autosave)
        self.history = options.get("history", self.history)
        if "concurrency" in options:
            self.parallel = options["concurrency"]
        self._options = options
        clear_dns_cache(negative_only=True)             # a name that failed a minute ago may resolve now
        for raw in urls:
            try:
                site = parse_site(raw)
            except ValueError:
                self.log(f"skipping '{raw}': not a valid web address")
                invalid.append(str(raw))
                continue
            with self.lock:
                company = self.companies.get(site.domain)
                if company and company["state"] in ACTIVE_STATES:
                    ignored.append(site.domain)
                    continue
                if company is None:
                    self.companies[site.domain] = self._new_company(site)
                    self.order.append(site.domain)
                else:
                    self._reset(site.domain)
                self.scans[site.domain] = _Scan(config)
                if start:
                    self._want_slot(site.domain)
            added.append(site.domain)
        self.emit("companies")
        self._fill_slots()
        return {"added": added, "ignored": ignored, "invalid": invalid}

    def _reset(self, domain):
        """Forget the last scan's results (call with the lock held)."""
        self.companies[domain].update(
            state="ready", jobs_count=0, new_count=0, missing_count=0, closed_count=0, outcome=None, outcome_detail=None,
            outcome_hint=None, outcome_short=None, careers_url=None, careers_note=None, phase=None, started_at=None,
            waiting_ahead=None, notes=[])
        self._jobs.pop(domain, None)

    def _want_slot(self, domain):
        """Put a company in line for a slot (call with the lock held)."""
        self.companies[domain].update(state="waiting")
        self._waiting.append(domain)

    def start_ready(self, options=None):
        """Manual mode's Start: everything "ready" goes in line. Returns how many were started."""
        options = clean_options(options) if options else self._options
        if "concurrency" in options:
            self.parallel = options["concurrency"]
        config = self._config_from(options)
        self.autosave = options.get("autosave", self.autosave)
        self.history = options.get("history", self.history)
        clear_dns_cache(negative_only=True)
        with self.lock:
            ready = [d for d in self.order if self.companies[d]["state"] == "ready"]
            for domain in ready:
                self.scans[domain] = _Scan(config)
                self._want_slot(domain)
        self.emit("companies")
        self._fill_slots()
        return len(ready)

    def start(self, urls, options=None):
        """Add the addresses and scan everything that is ready (or, if nothing is, everything again). False when a
        run is already going or there is nothing valid to scan. The command line uses this; the window uses submit()."""
        options = clean_options(options)
        if self.running:
            return False
        result = self.submit(urls, start=False, options=options)
        with self.lock:
            pending = [d for d in self.order if self.companies[d]["state"] in ("ready", "stopped", "failed")]
            if not pending:
                pending = list(self.order)                  # everything finished: pressing Start means "scan again"
        if not pending:
            return False
        config = self._config_from(options)
        with self.lock:
            for domain in pending:
                if self.companies[domain]["state"] != "ready":
                    self._reset(domain)
                self.scans[domain] = _Scan(config)
                self._want_slot(domain)
        self.emit("companies")
        self._fill_slots()
        return True

    # ---------- the slots ----------
    def _fill_slots(self):
        """Start waiting companies while slots are free (a slot is free while fewer than `parallel` are scanning)."""
        launch = []
        with self.lock:
            while self._waiting and len(self._active) < self.parallel:
                domain = self._waiting.popleft()
                self._active.add(domain)
                self.companies[domain].update(state="scanning", waiting_ahead=None, phase="Starting…", started_at=time.time())
                launch.append((domain, self.scans[domain]))
            for ahead, domain in enumerate(self._waiting):
                self.companies[domain]["waiting_ahead"] = ahead
        for domain, scan in launch:
            threading.Thread(target=self._run, args=(domain, scan), name=f"scrape-{domain}", daemon=True).start()
        if launch:
            self.emit("companies")
        self._settle()

    def _settle(self):
        """Keep `running`, the idle event and the status event in step with what is scanning or waiting."""
        with self.lock:
            running = bool(self._active or self._waiting)
            changed = running != self.running
            self.running = running
            (self._idle.clear if running else self._idle.set)()
        if changed:
            self.emit("status", running=running)

    def _release(self, domain, scan):
        with self.lock:
            if self.scans.get(domain) is scan:
                self._active.discard(domain)

    def _run(self, domain, scan):
        try:
            self._scrape_one(domain, scan)
        except Exception:
            log_exception(f"{domain}: scrape worker crashed")
        finally:
            if scan.timer:
                scan.timer.cancel()
            self._release(domain, scan)
            self._fill_slots()

    def stop(self, domain=None):
        """Stop everything that is scanning or waiting, or just one company. Each is finalized at once, with what it had
        found so far; a worker still busy in a slow request is abandoned and whatever it produces later is discarded.
        Returns whether anything was stopped."""
        with self.lock:
            targets = [d for d in ([domain] if domain else list(self.order))
                       if d in self.companies and self.companies[d]["state"] in ACTIVE_STATES]
            scans = [(d, self.scans[d]) for d in targets]
            for d in targets:
                if d in self._waiting:
                    self._waiting.remove(d)
        if domain and not scans:
            return False
        self.log(f"stopping {domain}" if domain else "stopping")
        for d, scan in scans:
            scan.token.cancel()
            self._finalize_stopped(d, scan)
        self._fill_slots()
        return True

    def _timed_out(self, domain, scan):
        scan.token.cancel(TIME_LIMIT)
        if self._finalize_stopped(domain, scan):
            self.log(f"{domain}: stopped at the time limit")
            self._release(domain, scan)
            self._fill_slots()

    def _finalize_stopped(self, domain, scan):
        """Record a scan as stopped (by the user or the time limit) unless it was already finalized. Returns True if
        this call did it."""
        with scan.lock:
            if scan.dead:
                return False
            scan.dead = True
            run_id = scan.run_id
        if scan.timer:
            scan.timer.cancel()
        count = self._read(domain, "jobs_count", 0)
        outcome = outcomes.TIMED_OUT if scan.token.reason == TIME_LIMIT else outcomes.STOPPED
        message, hint = outcomes.describe(outcome, domain, count=count, limit=scan.config.time_limit)
        self._update(domain, state="stopped")
        self._record_outcome(domain, outcome, message, hint, limit=scan.config.time_limit)
        if run_id:
            store.finish_run(run_id, "stopped", self._read(domain, "source"), count, self._read(domain, "new_count", 0),
                             outcome=outcome, outcome_detail=message)
        with self.lock:
            self._active.discard(domain)
        self.emit("companies")
        if count and self.autosave and self.history and run_id:
            self._write_files(domain, store.run_jobs(run_id), run_id)
        return True

    def wait(self, timeout=None):
        """Block until nothing is scanning or waiting (True), or the timeout passes (False)."""
        return self._idle.wait(timeout)

    # ---------- one company ----------
    def _set_phase(self, domain, scan, text):
        if scan.dead:
            return
        self._update(domain, phase=text)
        self.emit("phase", domain=domain, text=text)

    def _record_outcome(self, domain, outcome, message, hint=None, careers_url=None, careers_note=None, short=None,
                        limit=None):
        self._update(domain, outcome=outcome, outcome_detail=message, outcome_hint=hint, careers_url=careers_url,
                     careers_note=careers_note, phase=None, waiting_ahead=None,
                     outcome_short=short if short is not None else outcomes.short(outcome, limit=limit))

    def _scrape_one(self, domain, scan):
        token, config = scan.token, scan.config
        company = self._read(domain, "company") or domain
        run_id = store.start_run(domain, company, self.session_id) if self.history else None
        with scan.lock:
            scan.run_id = run_id
            dead = scan.dead
        self._update(domain, run_id=run_id)
        if dead:                                # stopped before the run was even recorded
            if run_id:
                store.finish_run(run_id, "stopped", None, 0, 0)
            return
        self.emit("reset", domain=domain)      # tell the UI to drop this company's old rows
        self.emit("companies")

        collected = []

        def on_jobs(batch):
            with scan.lock:
                if scan.dead:
                    return                     # stopped or timed out: nothing more is saved or shown
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

        # A company never runs past its time limit: at the limit it is finalized at once, keeping what it found.
        if config.time_limit:
            scan.timer = threading.Timer(config.time_limit, self._timed_out, args=(domain, scan))
            scan.timer.daemon = True
            scan.timer.start()
        ctx = ScanContext(config, token, log_sink=self.log, phase_sink=lambda text: self._set_phase(domain, scan, text))
        try:
            result = find_jobs(ctx, self._read(domain, "input") or domain, on_jobs=on_jobs)
        except Exception as error:
            log_exception(f"{domain}: scrape failed")
            with scan.lock:
                if scan.dead:
                    return
                scan.dead = True
            message, hint = outcomes.describe(outcomes.ERROR, domain)
            self._update(domain, state="failed", notes=[f"{type(error).__name__}: {error}"])
            self._record_outcome(domain, outcomes.ERROR, message, hint)
            if run_id:
                store.finish_run(run_id, "failed", None, len(collected), self._read(domain, "new_count", 0),
                                 outcome=outcomes.ERROR, outcome_detail=message)
            self.emit("companies")
            return
        finally:
            if scan.timer:
                scan.timer.cancel()

        with scan.lock:
            if scan.dead:
                return                         # Stop or the time limit got there first and recorded this scan
            scan.dead = True
        stopped = bool(result.get("stopped")) or token.is_set()
        state = "stopped" if stopped else "done"
        self._update(
            domain, company=result.get("company") or company, source=result.get("source"),
            source_detail=result.get("source_detail"), careers_page=(result.get("careers_pages") or [None])[0],
            notes=result.get("notes") or [], jobs_count=len(collected), state=state)
        outcome = result.get("outcome")
        message, hint = result.get("outcome_detail"), result.get("outcome_hint")
        if stopped and outcome not in (outcomes.STOPPED, outcomes.TIMED_OUT):        # Stop arrived as the scan ended
            outcome = None
        if outcome is None:
            outcome = (outcomes.TIMED_OUT if token.reason == TIME_LIMIT else outcomes.STOPPED) if stopped else outcomes.FOUND
            message, hint = outcomes.describe(outcome, domain, count=len(collected), limit=config.time_limit)
        self._record_outcome(domain, outcome, message, hint, result.get("careers_url"), result.get("careers_note"),
                             short=result.get("outcome_short"), limit=config.time_limit)
        if not self.history:
            with self.lock:
                self._jobs[domain] = collected
        if run_id:
            complete = state == "done" and not result.get("truncated")
            closing = store.finish_run(run_id, state, result.get("source"), len(collected),
                                       self._read(domain, "new_count", 0), complete=complete,
                                       outcome=outcome, outcome_detail=message)
            if closing["closed"] or closing["missing"]:
                self._update(domain, missing_count=closing["missing"], closed_count=closing["closed"])
                self.log(f"{domain}: {closing['closed']} posting(s) closed, {closing['missing']} no longer listed")

        if collected and self.autosave:
            self._write_files(domain, collected, run_id)
        self.emit("companies")

    def _write_files(self, domain, jobs, run_id=None):
        try:
            if self.exporter:
                self.exporter(domain, run_id, jobs)
            else:
                export_jobs(domain, jobs)
        except Exception as error:
            self.log(f"could not write output for {domain}: {error}")
