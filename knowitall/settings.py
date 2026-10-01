"""User settings, kept in the app-data folder (settings.json) so they survive restarts and are shared by the
window and the command line. (The browser's localStorage cannot do this job: its origin includes the random
port, so it starts empty on every launch.)

Every value is validated on the way in and on the way out; unknown keys are dropped and bad values fall back to
the default, so a hand-edited or damaged file can never break start-up.
"""
import json
import os
import tempfile
from pathlib import Path

from . import paths

DEFAULTS = {
    "theme": "dark",            # "dark" | "light"
    "max_jobs": 2000,           # cap per company for very large boards
    "max_enrich": 50,           # job pages opened for details on self-hosted sites
    "concurrency": 3,           # companies scraped at once (1-3): the "At once" control
    "parallel_mode": "auto",    # "auto": a scan starts as soon as a slot is free; "manual": addresses wait for Start
    "cache_reuse": "12h",       # how long downloaded pages are reused: "off", "1h" or "12h" (was the "fresh" switch)
    "autosave": True,           # write JSON/CSV files as each company finishes
    "auto_backup": True,        # back the database up at every launch (when it changed since the last automatic one)
    "export_scope": "matching", # exports hold "matching" postings only (the active filters) or "everything"
    "browser_workers": 1,       # scraping Chrome windows that may render at once (1-3), used when auto is off
    "browser_workers_auto": True,  # let the number of Chrome windows follow "At once" (at most 2)
    "time_limit_min": 3,        # minutes one company may take before its scan is cut off (1, 3 or 5)
    "queue_panel_open": True,   # the queue panel beside the rail is open (the rail's Queue button toggles it)
    "filters": {},              # the saved filter profile (see PERSISTED_LISTS); edited through /api/filters, not /api/settings
}
PERSISTED_LISTS = ("workplace", "employment_type", "country", "region_group", "department", "source")
INT_LIMITS = {"max_jobs": (1, 100_000), "max_enrich": (0, 500), "concurrency": (1, 3), "browser_workers": (1, 3)}
CHOICES = {"time_limit_min": (1, 3, 5)}
BOOLEANS = ("autosave", "auto_backup", "browser_workers_auto", "queue_panel_open")
EXPORT_SCOPES = ("matching", "everything")
CACHE_REUSE = ("off", "1h", "12h")
PARALLEL_MODES = ("auto", "manual")
THEMES = ("dark", "light")


def clean(values):
    """A complete, valid settings dict built from whatever was given (missing/bad entries take the default)."""
    values = values if isinstance(values, dict) else {}
    result = dict(DEFAULTS)
    if values.get("theme") in THEMES:
        result["theme"] = values["theme"]
    if values.get("cache_reuse") in CACHE_REUSE:
        result["cache_reuse"] = values["cache_reuse"]
    elif values.get("fresh") is True:
        result["cache_reuse"] = "off"                   # a settings file from before: "fresh data" meant do not reuse pages
    if values.get("export_scope") in EXPORT_SCOPES:
        result["export_scope"] = values["export_scope"]
    if values.get("parallel_mode") in PARALLEL_MODES:
        result["parallel_mode"] = values["parallel_mode"]
    for key, (low, high) in INT_LIMITS.items():
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            continue
        result[key] = max(low, min(high, int(value)))
    result["filters"] = clean_filters(values.get("filters"))
    for key, allowed in CHOICES.items():
        value = values.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value in allowed:
            result[key] = value
    for key in BOOLEANS:
        if isinstance(values.get(key), bool):
            result[key] = values[key]
    return result


def clean_filters(raw):
    """The saved filter profile: lists of text for the chip filters and a number of days for Posted. Anything else is dropped."""
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for key in PERSISTED_LISTS:
        values = raw.get(key)
        if isinstance(values, list):
            kept = [v.strip() for v in values if isinstance(v, str) and v.strip()][:200]
            if kept:
                result[key] = kept
    days = raw.get("posted_within_days")
    if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 3650:
        result["posted_within_days"] = days
    return result


def validate(update):
    """Check a partial update from a client; returns the cleaned partial, or raises ValueError naming the problem."""
    if not isinstance(update, dict):
        raise ValueError("settings must be a JSON object")
    problems = []
    accepted = {}
    for key, value in update.items():
        if key not in DEFAULTS:
            problems.append(f"unknown setting: {key}")
        elif key == "theme":
            if value in THEMES:
                accepted[key] = value
            else:
                problems.append(f"theme must be one of {', '.join(THEMES)}")
        elif key == "cache_reuse":
            if value in CACHE_REUSE:
                accepted[key] = value
            else:
                problems.append(f"cache_reuse must be one of {', '.join(CACHE_REUSE)}")
        elif key == "export_scope":
            if value in EXPORT_SCOPES:
                accepted[key] = value
            else:
                problems.append(f"export_scope must be one of {', '.join(EXPORT_SCOPES)}")
        elif key == "parallel_mode":
            if value in PARALLEL_MODES:
                accepted[key] = value
            else:
                problems.append(f"parallel_mode must be one of {', '.join(PARALLEL_MODES)}")
        elif key in CHOICES:
            if isinstance(value, int) and not isinstance(value, bool) and value in CHOICES[key]:
                accepted[key] = value
            else:
                problems.append(f"{key} must be one of {', '.join(str(v) for v in CHOICES[key])}")
        elif key in INT_LIMITS:
            low, high = INT_LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                problems.append(f"{key} must be a whole number from {low} to {high}")
            else:
                accepted[key] = value
        elif isinstance(value, bool):
            accepted[key] = value
        else:
            problems.append(f"{key} must be true or false")
    if problems:
        raise ValueError("; ".join(problems))
    return accepted


def load(path=None):
    path = Path(path or paths.SETTINGS_PATH)
    try:
        return clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(values, path=None):
    """Write atomically (temp file + rename) so a crash mid-write cannot leave a half-written settings file."""
    path = Path(path or paths.SETTINGS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix="settings-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(clean(values), file, indent=2)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
