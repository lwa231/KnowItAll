"""Run manager: the queue, concurrency, Stop, and the event stream the UI polls."""
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import store
from .discovery import parse_site
from .fetch import settings
from .scraper import find_jobs, write_csv
from .normalize import dedupe

from botasaurus import bt


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self.companies = {}        # domain -> company state shown in the UI
        self.order = []            # queue order
        self.events = []           # append-only; the UI polls by cursor
        self.running = False
        self.cancel = threading.Event()
        self.pool = None
        self.started_at = None
        settings.cancel_event = self.cancel
        settings.log_sink = self.log

    # ---------- events ----------
    def emit(self, kind, **payload):
        with self.lock:
            self.events.append({"i": len(self.events), "kind": kind, **payload})
            if len(self.events) > 20000:                  # keep memory bounded on long sessions
                self.events = self.events[-10000:]

    def log(self, message):
        self.emit("log", text=str(message))

    def events_since(self, cursor):
        with self.lock:
            return [e for e in self.events if e["i"] >= cursor], len(self.events)

    # ---------- state ----------
    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "companies": [dict(c, jobs=None) for c in (self.companies[d] for d in self.order)],
            }

    def _company(self, domain):
        return self.companies.setdefault(domain, {
            "domain": domain, "company": domain, "state": "queued", "jobs_count": 0,
            "new_count": 0, "source": None, "careers_page": None, "run_id": None, "notes": [],
        })

    # ---------- control ----------
    def queue(self, urls):
        added = []
        for raw in urls:
            try:
                site = parse_site(raw)
            except ValueError:
                self.log(f"skipping '{raw}': not a valid web address")
                continue
            if site.domain in self.companies:
                continue
            with self.lock:
                self._company(site.domain).update(company=site.name, input=site.input_url)
                self.order.append(site.domain)
            added.append(site.domain)
        self.emit("companies")
        return added

    def start(self, urls, options):
        if self.running:
            return False
        settings.cache = "REFRESH" if options.get("fresh") else True
        settings.max_jobs = int(options.get("max_jobs") or 2000)
        settings.max_enrich = int(options.get("max_enrich") or 50)
        concurrency = max(1, min(4, int(options.get("concurrency") or 3)))
        self.autosave = options.get("autosave", True)

        self.queue(urls)
        pending = [d for d in self.order if self.companies[d]["state"] in ("queued", "stopped", "failed")]
        if not pending:
            # Everything in the queue has already finished: pressing Start means "scan them again".
            pending = list(self.order)
        if not pending:
            return False
        for domain in pending:
            self.companies[domain].update(state="queued", jobs_count=0, new_count=0)

        self.cancel.clear()
        self.running = True
        self.emit("status", running=True)
        self.pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="scrape")
        threading.Thread(target=self._drive, args=(pending, concurrency), daemon=True).start()
        return True

    def stop(self):
        self.cancel.set()
        self.log("stopping — finishing the current step")

    def _drive(self, pending, concurrency):
        try:
            futures = [self.pool.submit(self._scrape_one, d) for d in pending]
            for f in futures:
                try:
                    f.result()
                except Exception:
                    traceback.print_exc()
        finally:
            self.running = False
            self.pool.shutdown(wait=False)
            self.emit("status", running=False)

    # ---------- one company ----------
    def _scrape_one(self, domain):
        state = self.companies[domain]
        if self.cancel.is_set():
            state["state"] = "stopped"
            self.emit("companies")
            return

        state.update(state="scanning", jobs_count=0, new_count=0)
        run_id = store.start_run(domain, state.get("company") or domain)
        state["run_id"] = run_id
        self.emit("reset", domain=domain)      # tell the UI to drop this company's old rows
        self.emit("companies")

        collected = []

        def on_jobs(batch):
            batch = [dict(j) for j in batch]
            new_count = store.mark_new(batch, domain)
            store.save_jobs(run_id, batch)
            collected.extend(batch)
            state["jobs_count"] = len(collected)
            state["new_count"] = state.get("new_count", 0) + new_count
            self.emit("jobs", domain=domain, jobs=batch, total=len(collected))

        try:
            result = find_jobs(state.get("input") or domain, on_jobs=on_jobs)
        except Exception as error:
            traceback.print_exc()
            state.update(state="failed", notes=[f"{type(error).__name__}: {error}"])
            store.finish_run(run_id, "failed", None, 0, 0)
            self.emit("companies")
            return

        state["company"] = result.get("company") or state["company"]
        state["source"] = result.get("source")
        state["source_detail"] = result.get("source_detail")
        state["careers_page"] = (result.get("careers_pages") or [None])[0]
        state["notes"] = result.get("notes") or []
        jobs = dedupe(collected)
        state["jobs_count"] = len(jobs)

        stopped = bool(result.get("stopped")) or self.cancel.is_set()
        state["state"] = "stopped" if stopped and not jobs else ("stopped" if stopped else "done")
        store.finish_run(run_id, state["state"], state.get("source"), len(jobs), state.get("new_count", 0))

        if jobs and getattr(self, "autosave", True):
            self._write_files(domain, jobs)
        self.emit("companies")

    def _write_files(self, domain, jobs):
        name = f"jobs_{domain}"
        try:
            bt.write_json(jobs, name, log=False)
            write_csv(jobs, f"output/{name}.csv")
        except Exception as error:
            self.log(f"could not write output for {domain}: {error}")


runner = Runner()
