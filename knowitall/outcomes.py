"""How a company's scan ended, and the words for it.

Every scan ends in exactly one outcome, so a company is never left "scanning" and never silently empty. The
window and the command line read the same message and hint from here (the wording is in one place):

  found            at least one posting was read
  no_listings      a careers page was found, but no postings could be read from it
  no_careers_page  no careers page was found at all
  unreachable      the address does not answer (DNS or connection failure)
  blocked          the site refused automated access, even to a real browser
  unsupported      careers are hosted on a platform KnowItAll has no reader for yet
  timed_out        the per-company time limit ran out (what was found is kept)
  stopped          the user pressed Stop (what was found is kept)
  error            something unexpected went wrong (details are in the log file)
"""
FOUND, NO_LISTINGS, NO_CAREERS_PAGE, UNREACHABLE, BLOCKED = "found", "no_listings", "no_careers_page", "unreachable", "blocked"
UNSUPPORTED, TIMED_OUT, STOPPED, ERROR = "unsupported", "timed_out", "stopped", "error"
OUTCOMES = (FOUND, NO_LISTINGS, NO_CAREERS_PAGE, UNREACHABLE, BLOCKED, UNSUPPORTED, TIMED_OUT, STOPPED, ERROR)

# The outcomes that mean "a scan finished but gave nothing to look at": the All pane lists these.
EMPTY_OUTCOMES = (NO_LISTINGS, NO_CAREERS_PAGE, UNREACHABLE, BLOCKED, UNSUPPORTED)

PLATFORMS = {
    "eightfold": "Eightfold", "icims": "iCIMS", "successfactors": "SuccessFactors", "taleo": "Taleo",
    "phenom": "Phenom", "jobvite": "Jobvite", "workable": "Workable", "bamboohr": "BambooHR",
    "oracle": "Oracle Recruiting",
}


def postings_text(count):
    return f"{count:,} posting{'' if count == 1 else 's'}"


def limit_text(seconds):
    return f"{int(seconds // 60)} min" if seconds >= 60 and seconds % 60 == 0 else f"{int(seconds)} s"


def describe(outcome, domain, *, count=0, platform=None, limit=None, board=None):
    """(message, hint) for an outcome. `limit` is the time limit in seconds; `board` names a job board
    (LinkedIn, Indeed) that is where a company lists its jobs; `platform` is a key of PLATFORMS."""
    if outcome == FOUND:
        return postings_text(count), None
    if outcome == NO_LISTINGS:
        if board:
            return f"{domain} lists its jobs on {board}, which KnowItAll doesn't scan.", None
        return ("No listings available on this page.",
                f"KnowItAll found {domain}'s careers page but couldn't read any job postings from it.")
    if outcome == NO_CAREERS_PAGE:
        return (f"No careers page found on {domain}.",
                "Try the company's main website or its careers site address.")
    if outcome == UNREACHABLE:
        return (f"Couldn't reach {domain}.", "Check the address or your internet connection, then scan again.")
    if outcome == BLOCKED:
        return f"{domain} blocked automated access.", "The site's bot protection stopped the scan."
    if outcome == UNSUPPORTED:
        name = PLATFORMS.get(platform, platform or "a hiring platform")
        return f"{domain} uses {name}, which KnowItAll can't read yet.", None
    if outcome == TIMED_OUT:
        after = f" after {limit_text(limit)}" if limit else ""
        return f"Stopped{after} — this site is slow. {postings_text(count)} found.", None
    if outcome == STOPPED:
        return f"Stopped. {postings_text(count)} found.", None
    if outcome == ERROR:
        return f"Something went wrong scanning {domain}.", "Details are in the log."
    return outcome, None


def careers_host_note(domain, host):
    return f"Careers for {domain} are hosted on {host}."


def short(outcome, *, platform=None, limit=None, host=None):
    """A few words for a crowded place (a sidebar row): "blocked", "no careers page", "3 min limit". None when the
    outcome needs no words (postings were found and nothing is unusual)."""
    if outcome == FOUND:
        return f"careers on {host}" if host else None
    return {
        NO_LISTINGS: "no listings",
        NO_CAREERS_PAGE: "no careers page",
        UNREACHABLE: "couldn't reach",
        BLOCKED: "blocked",
        UNSUPPORTED: f"uses {PLATFORMS.get(platform, platform or 'unsupported platform')}",
        TIMED_OUT: f"{limit_text(limit)} limit" if limit else "time limit",
        STOPPED: "stopped",
        ERROR: "error, see the log",
    }.get(outcome)
