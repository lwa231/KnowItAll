"""One module per applicant tracking system (ATS).

Each module exposes `fetch_jobs(detection, site) -> list[dict] | None`, where None means
"this board doesn't exist / isn't usable". Modules that support guessing a board from the
company name also expose `board_name(detection) -> str | None` for verification.
"""
from . import ashby, greenhouse, lever, oracle, smartrecruiters, workday

MODULES = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "oracle": oracle,
}

LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "smartrecruiters": "SmartRecruiters",
          "workday": "Workday", "oracle": "Oracle Recruiting"}

# Boards we try by company name when nothing is linked (misses return a clean 404).
GUESSABLE = ["greenhouse", "lever", "ashby"]
