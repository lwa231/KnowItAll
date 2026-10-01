"""Writing results to disk: jobs_<domain>.json / .csv in the exports folder, and the whole-session export.

Every file is written to a temporary name and moved into place, so a crash never leaves half a file and two files of one
export never disagree. If the destination is locked (Excel has the CSV open on Windows), the new file gets a numbered
name ("jobs_x (1).csv") instead of failing silently, and the caller is told.

Text that came from a website is never trusted in a spreadsheet: a cell that starts with = + - @ (or a tab or carriage
return) would be run as a formula when the CSV is opened, so it is prefixed with an apostrophe.
"""
import csv
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import paths
from .normalize import FIELDS

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def _numbered(path, n):
    path = Path(path)
    return path.with_name(f"{path.stem} ({n}){path.suffix}")


def _atomic_write(path, write, on_renamed=None, newline=None, encoding="utf-8"):
    """write(file) to a temp file beside `path`, then move it over `path`. Returns the path actually written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", newline=newline, encoding=encoding) as file:
            write(file)
        target, n = path, 0
        while True:
            try:
                os.replace(temp, target)
                break
            except PermissionError:                        # open in another program (Windows)
                n += 1
                target = _numbered(path, n)
                if n > 20:
                    raise
        if target != path and on_renamed:
            on_renamed(path, target)
        return target
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def write_csv(jobs, path, on_renamed=None):
    def write(file):                                       # utf-8-sig so Excel on Windows shows accented characters correctly
        writer = csv.DictWriter(file, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: safe_cell(job.get(k)) for k in FIELDS} for job in jobs)
    return _atomic_write(path, write, on_renamed, newline="", encoding="utf-8-sig")


def write_json(jobs, path, meta=None, on_renamed=None):
    """A plain list of jobs; when the export was filtered, {"filters": ..., "jobs": [...]} so the file says what it holds."""
    payload = {**meta, "jobs": jobs} if meta else jobs
    return _atomic_write(path, lambda file: json.dump(payload, file, indent=4), on_renamed)


def write_note(path, lines, on_renamed=None):
    return _atomic_write(path, lambda file: file.write("\n".join(lines) + "\n"), on_renamed)


def export_jobs(domain, jobs, filter_summary=None, total=None, on_renamed=None):
    """Write jobs_<domain>.json and .csv (named ..._filtered when filters applied; a .txt beside the CSV says which).
    Returns the paths written."""
    paths.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = paths.EXPORTS_DIR / f"jobs_{domain}{'_filtered' if filter_summary else ''}"
    meta = {"filters": filter_summary, "matching": len(jobs), "total": total} if filter_summary else None
    written = [write_json(jobs, f"{stem}.json", meta, on_renamed), write_csv(jobs, f"{stem}.csv", on_renamed)]
    if filter_summary:
        write_note(f"{stem}.txt", [f"Filters: {filter_summary}", f"Postings: {len(jobs)}" + (f" of {total}" if total is not None else "")], on_renamed)
    return [str(p) for p in written]


def export_all(jobs, name="jobs_all_companies", on_renamed=None):
    """Every job of the given list in one CSV. Returns the path."""
    paths.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return str(write_csv(jobs, paths.EXPORTS_DIR / f"{name}.csv", on_renamed))


def export_session(jobs, filter_summary=None, total=None, now=None, on_renamed=None):
    """This session's postings in one CSV: knowitall_session_YYYY-MM-DD_HHMM[_filtered].csv. Returns the path."""
    paths.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M")
    stem = paths.EXPORTS_DIR / f"knowitall_session_{stamp}{'_filtered' if filter_summary else ''}"
    written = write_csv(jobs, f"{stem}.csv", on_renamed)
    if filter_summary:
        write_note(f"{stem}.txt", [f"Filters: {filter_summary}", f"Postings: {len(jobs)}" + (f" of {total}" if total is not None else "")], on_renamed)
    return str(written)
