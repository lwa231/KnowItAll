"""Where KnowItAll keeps things.

  DATA_DIR     history.db, the page cache and logs  (per-user app-data folder, never the program folder)
  EXPORTS_DIR  the jobs_*.json / .csv files users open (~/Documents/KnowItAll/exports)
  asset_dir()  read-only files that ship with the program (the ui/ folder)

KNOWITALL_HOME moves everything into one folder (handy for tests and portable installs);
KNOWITALL_EXPORTS overrides just the exports folder; KNOWITALL_LEGACY_DIR says where to look for
data left by older versions (default: next to the code).
"""
import logging
import os
import shutil
import sys
from pathlib import Path

from platformdirs import user_data_dir

from . import logsetup

APP_NAME = "KnowItAll"
_log = logging.getLogger(logsetup.LOGGER_NAME)


def _resolve():
    home = os.environ.get("KNOWITALL_HOME")
    data = Path(home).expanduser() if home else Path(user_data_dir(APP_NAME, appauthor=False))
    exports = os.environ.get("KNOWITALL_EXPORTS")
    if exports:
        exports = Path(exports).expanduser()
    elif home:
        exports = data / "exports"
    else:
        exports = Path.home() / "Documents" / APP_NAME / "exports"
    return data, exports


DATA_DIR, EXPORTS_DIR = _resolve()
DB_PATH = DATA_DIR / "history.db"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
SETTINGS_PATH = DATA_DIR / "settings.json"


def code_dir():
    """The folder holding ui.py / main.py (PyInstaller's unpack folder when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def asset_dir():
    return code_dir()


def legacy_dir():
    override = os.environ.get("KNOWITALL_LEGACY_DIR")
    return Path(override).expanduser() if override else code_dir()


def migrate_legacy(legacy, data_dir, exports_dir):
    """Move data that older versions wrote next to the code into the new locations.

    Never overwrites anything already at the destination. Returns a list of what was moved.
    """
    moved = []

    def move(source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(f"{source} -> {target}")

    old_db = legacy / "history.db"
    if old_db.is_file() and not (data_dir / "history.db").exists():
        for suffix in ("", "-wal", "-shm"):          # a WAL database is three files that belong together
            part = legacy / f"history.db{suffix}"
            if part.is_file():
                move(part, data_dir / part.name)

    old_cache, new_cache = legacy / "cache", data_dir / "cache"
    if old_cache.is_dir() and not (new_cache.exists() and any(new_cache.iterdir())):
        if new_cache.exists():
            new_cache.rmdir()                        # an empty folder must not block the move
        move(old_cache, new_cache)

    old_output = legacy / "output"
    if old_output.is_dir():
        for item in sorted(old_output.glob("jobs_*.*")):
            if not (exports_dir / item.name).exists():
                move(item, exports_dir / item.name)
        try:
            old_output.rmdir()                       # only succeeds when nothing is left inside
        except OSError:
            pass
    return moved


def enter_data_dir():
    """Create the folders, start the file log, move old data over, and chdir to DATA_DIR.

    The chdir is deliberate: botasaurus resolves its cache/ folder against the working directory.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logsetup.init(LOG_DIR)
    legacy = legacy_dir()
    if legacy.resolve() != DATA_DIR.resolve():
        try:
            for line in migrate_legacy(legacy, DATA_DIR, EXPORTS_DIR):
                _log.info("moved old data: %s", line)
                print(f"[knowitall] moved old data: {line}", flush=True)
        except OSError as error:
            _log.warning("could not move old data: %s", error)
    for folder in (CACHE_DIR, LOG_DIR, EXPORTS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    os.chdir(DATA_DIR)
    return DATA_DIR
