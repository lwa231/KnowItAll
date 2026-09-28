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
    "concurrency": 3,           # companies scraped at once (1-4)
    "fresh": False,             # ignore cached pages by default
    "autosave": True,           # write JSON/CSV files as each company finishes
    "browser_workers": 1,       # scraping Chrome windows that may render at once (1-3)
}
INT_LIMITS = {"max_jobs": (1, 100_000), "max_enrich": (0, 500), "concurrency": (1, 4), "browser_workers": (1, 3)}
BOOLEANS = ("fresh", "autosave")
THEMES = ("dark", "light")


def clean(values):
    """A complete, valid settings dict built from whatever was given (missing/bad entries take the default)."""
    values = values if isinstance(values, dict) else {}
    result = dict(DEFAULTS)
    if values.get("theme") in THEMES:
        result["theme"] = values["theme"]
    for key, (low, high) in INT_LIMITS.items():
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            continue
        result[key] = max(low, min(high, int(value)))
    for key in BOOLEANS:
        if isinstance(values.get(key), bool):
            result[key] = values[key]
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
