"""What a scan needs to know, passed explicitly down the call chain (no module-level settings).

  RunConfig    immutable options for one Start: cache mode, caps, whether Chrome may be used
  CancelToken  Stop, for everything or for one company: a company's token also reports cancelled when
               the run-wide token it hangs off is cancelled. It remembers why: "stop" (the user) or
               "time_limit" (the per-company limit ran out), so the outcome can say which.
  ScanContext  one company's scan: its config, its cancel token, and where its log lines and progress
               phases ("Reading Greenhouse board...") go
"""
import threading
from dataclasses import dataclass, replace
from typing import Callable, Optional

from .fetch import log as _log


@dataclass(frozen=True)
class RunConfig:
    cache: object = True                       # True, or "REFRESH" to ignore cached pages and re-download them
    max_jobs: int = 2000                       # cap for very large boards (e.g. Workday tenants)
    max_enrich: int = 50                       # job pages to open when reading JSON-LD in the generic fallback
    use_browser: bool = True                   # allow the Chrome fallback at all
    headless: bool = True                      # only used by the standalone renderer (no renderer supplied)
    renderer: Optional[Callable] = None        # callable(url, should_cancel) -> page dict, e.g. BrowserPool.render
    reuse: str = "12h"                        # how long downloaded pages are reused: "1h" or "12h" (cache="REFRESH" is "off")
    debug_payloads: bool = False               # log what JSON a rendered page loaded (to build per-site adapters)
    time_limit: Optional[int] = None           # seconds a company may take (the runner enforces it; this is for wording)

    def with_(self, **changes):
        return replace(self, **changes)


STOP, TIME_LIMIT = "stop", "time_limit"


class CancelToken:
    def __init__(self, parent=None):
        self._event = threading.Event()
        self._parent = parent
        self._reason = None

    def cancel(self, reason=STOP):
        """Cancel; the first reason given is the one that sticks."""
        if self._reason is None:
            self._reason = reason
        self._event.set()

    def is_set(self):
        return self._event.is_set() or (self._parent is not None and self._parent.is_set())

    @property
    def reason(self):
        """Why this token is cancelled ("stop" or "time_limit"), or None while it is not."""
        if self._reason is not None:
            return self._reason
        return self._parent.reason if self._parent is not None else (STOP if self._event.is_set() else None)

    def child(self):
        """A token that can be cancelled on its own, and is also cancelled when this one is."""
        return CancelToken(parent=self)


class ScanContext:
    def __init__(self, config=None, cancel=None, log_sink=None, on_jobs=None, flags=None, phase_sink=None):
        self.config = config or RunConfig()
        self.cancel = cancel or CancelToken()
        self.log_sink = log_sink
        self.on_jobs = on_jobs                  # callable(list_of_jobs): readers hand over results as they arrive
        self.flags = flags if flags is not None else {}     # shared by every copy of this context
        self.phase_sink = phase_sink            # callable(text): what the scan is doing right now

    def should_stop(self):
        return self.cancel.is_set()

    def log(self, message):
        _log(message)
        if self.log_sink:
            try:
                self.log_sink(str(message))
            except Exception:
                pass                            # a failing UI log must never break a scrape

    def emit(self, jobs):
        """Hand a batch of jobs to whoever is listening, as soon as a page of results is in."""
        if self.on_jobs and jobs:
            self.on_jobs(jobs)

    def phase(self, text):
        """Tell the UI what this scan is doing now ("Reading Greenhouse board..."). Never raises."""
        self.flags["phase"] = text
        if self.phase_sink:
            try:
                self.phase_sink(str(text))
            except Exception:
                pass

    def with_config(self, **changes):
        return ScanContext(self.config.with_(**changes), self.cancel, self.log_sink, self.on_jobs, self.flags,
                           self.phase_sink)

    def with_on_jobs(self, on_jobs):
        return ScanContext(self.config, self.cancel, self.log_sink, on_jobs, self.flags, self.phase_sink)

    # ---------- what the scan learned about the site, for the final outcome ----------
    def note(self, key, value):
        """Remember a fact about this scan (e.g. a careers page that stayed blocked). Lists accumulate."""
        current = self.flags.setdefault("notes", {})
        current.setdefault(key, [])
        if value not in current[key]:
            current[key].append(value)

    def noted(self, key):
        return list(self.flags.get("notes", {}).get(key, []))

    def mark_truncated(self, why):
        """Record that the listing was cut short (a size cap), so the scan is not treated as complete."""
        self.flags["truncated"] = why

    @property
    def truncated(self):
        return self.flags.get("truncated")
