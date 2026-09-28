"""Writing results to disk: jobs_<domain>.json / .csv in the exports folder, and the one-file export."""
import csv
import json

from . import paths
from .normalize import FIELDS


def write_csv(jobs, path):
    # utf-8-sig so Excel on Windows shows accented characters correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(jobs)


def write_json(jobs, path):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(jobs, file, indent=4)


def export_jobs(domain, jobs):
    """Write jobs_<domain>.json and .csv into the exports folder. Returns the two paths."""
    paths.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = paths.EXPORTS_DIR / f"jobs_{domain}"
    write_json(jobs, f"{stem}.json")
    write_csv(jobs, f"{stem}.csv")
    return [f"{stem}.json", f"{stem}.csv"]


def export_all(jobs, name="jobs_all_companies"):
    """Every job of the given list in one CSV. Returns the path."""
    paths.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.EXPORTS_DIR / f"{name}.csv"
    write_csv([{k: job.get(k) for k in FIELDS} for job in jobs], target)
    return str(target)
