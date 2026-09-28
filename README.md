# KnowItAll
Web-Scraper built on the [botasaurus](https://github.com/omkarcloud/botasaurus) framework by omkarcloud.

Give it a company's web address and it finds that company's job listings — **title, link, location, department, posted date**, and whether the posting is one you have never seen before. Results stream into a desktop interface and are saved as JSON and CSV.

There are two ways to run it: the **desktop UI** (`python ui.py`, or the packaged exe) and the **command line** (`python main.py stripe.com`). Both use the same scraper.

## Setup

Requires **Python 3.9+**. The interface runs in its own native window (pywebview), not in Chrome. **Google Chrome is optional**: the scraper starts a hidden headless Chrome only for JavaScript-heavy sites; without it those sites are skipped and the UI shows a notice. On Windows the Microsoft Edge WebView2 Runtime is required for the window (it ships with Windows 11).

### Windows 10 (including a Hyper-V VM)
```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python ui.py
```
If `python` isn't found, install Python from python.org and tick **"Add python.exe to PATH"**, or use `py -m venv venv`.
If PowerShell refuses to run `activate`, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### macOS
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python ui.py
```

The first run downloads a small helper binary used by botasaurus' HTTP client. That is normal.

## The desktop app

`python ui.py` starts a local server and opens the interface in its own native window. The window is never Chrome and is completely separate from the scraper's browser. When a page needs JavaScript the scraper uses a hidden headless Chrome that starts on demand and closes after 2 minutes idle (`--browser-workers 2` or `3` lets renders run in parallel; `--no-browser-scraping` disables it). Only one copy of the app can run per user. The window remembers its size and position, asks before quitting mid-scan, and `--debug` enables developer tools. Close the window (or press Ctrl+C in the terminal) and everything shuts down.

Your data lives outside the program folder: history, cache and logs in the per-user app-data folder (`~/Library/Application Support/KnowItAll` on macOS, `%LOCALAPPDATA%\KnowItAll` on Windows) and exported CSV/JSON in `~/Documents/KnowItAll/exports`. Set `KNOWITALL_HOME` to keep everything in one folder instead. Data left next to the code by older versions is moved over on first launch.

**Running a scan.** Type one or more addresses into the box (space or comma separated) and press **Start**, or **+ Queue** to line them up without starting. Up to 4 companies scrape at once. **Stop** halts the run (or, with the ■ beside a company in the queue, just that company) and keeps every row already found.

**Finding postings.** The filter bar under the header searches every company at once (or just the open tab). Type words to narrow (`engineer remote`), `"quoted phrases"`, `-word` to exclude, `a OR b`; places expand, so `nyc`, `germany`, `bay area` and `EMEA` find their cities and countries. The chips filter by workplace, employment type, region or country, department, source and how recently posted, each showing how many postings it would leave. **Listed | Gone | Closed** switches between what the latest scan found and what has since disappeared (gone after one complete scan without it, closed after two). Results load as you scroll, and a company that streams in keeps appearing live.

**Other views.** *History* lists every scan and opens any of them; *Output* the exported files; *Settings* the defaults (saved on disk, shared with the command line); *System* shows whether the hidden Chrome works, how many pages each request client served, storage use with **Clear cache** and **Compact database**, and where everything is kept. Warnings such as "Chrome could not be started" appear in a notice bar above the results.

The interface is plain HTML/CSS/JavaScript with no build step; see [ui/README.md](ui/README.md) for how it is organised and how to change it. Text is at least 12px, both themes meet WCAG AA contrast, and everything is reachable from the keyboard (press `/` to jump to the search box).

**The results table.** Rows fill in as they arrive. Company is the grouping: in the **All** tab each company is a foldable folder showing its source, posting count, new count, departments, locations and state. Click a column header to sort (grouped mode sorts within each company), and use the filter box to narrow by title, location or department.

**Arranging panes.** Drag a company tab out of the strip:
- Drop it in a **quadrant band** (the outer quarter of the workspace, on any side) to snap it. Sizing follows occupancy — one tile fills the space, two split 50/50, three or four become quarters. A live highlight shows exactly what you will get.
- Drop it **anywhere else** and it becomes a free-floating window you position and size yourself. Floats stop at 420×180 so the table stays readable.
- Every pane folds into its title bar with the caret, and `×` returns it to the tab strip.

**History.** Every run is stored. The History view lists them with dates and counts; click one to reopen its results.

**New postings.** A green `new` marks a posting whose link has never appeared in any previous run, of any company. That set of hashes lives in `history.db`, so the second scan of a company shows only what actually changed.

**Settings** holds the run defaults (max jobs, detail depth, companies at once, fresh data), output options, and the light/dark toggle. They persist in the browser.

## Command line

```bash
python main.py <web address> [<web address> ...] [options]
```

| Option | What it does |
|---|---|
| `--no-cache` | Re-download everything instead of using pages cached in the last 12 hours |
| `--max-jobs N` | Cap per company for huge boards (default 2000) |
| `--max-enrich N` | Job pages opened for details on self-hosted sites (default 50) |
| `--no-browser` | Never use Chrome |
| `--headful` | Show the Chrome window when the fallback runs |
| `--parallel N` | Scrape N companies at once |
| `--show N` | Jobs to print per company (default 10) |

## How the scraper works

1. **Find the careers page** — homepage links, common paths (`/careers`, `/jobs`, …), the `careers.`/`jobs.` subdomains, then the sitemap.
2. **Detect the hiring platform** and read its official public feed, which is fast and complete:

   | Platform | Verified against |
   |---|---|
   | Greenhouse | figma.com, stripe.com, airbnb.com |
   | Lever | palantir.com |
   | Ashby | ramp.com, linear.app |
   | Workday | nvidia.com |
   | SmartRecruiters | ServiceNow's board |

   If a careers page never names its platform, the company name is tried as a board name (that is how Stripe and Airbnb are found) and only accepted when the board's own title matches the company.
3. **Otherwise read the page** — job-looking links, following "View all jobs" and numbered result pages, then opening up to 50 postings for their embedded `JobPosting` data.
4. **Chrome fallback** for pages that block plain requests or only render with JavaScript.

Pages are cached for 12 hours in the app's `cache/` folder.

## Output

Each company writes `jobs_<domain>.json` and `.csv` to the exports folder (the CSV opens directly in Excel). The Output view lists what has been written, opens the folder, and exports the whole queue as one file.

| Field | Meaning |
|---|---|
| `company` | Company name |
| `title` | Job title |
| `url` | Link to the posting |
| `location` | Location(s) as the site gives them, separated by `\|` |
| `remote` | `True` fully remote, `False` on-site or hybrid, empty unknown (derived from `workplace`) |
| `department` | Department or team where the site provides it |
| `posted` | When the posting went up (ISO 8601) |
| `source` | `greenhouse`, `lever`, `ashby`, `workday`, `smartrecruiters`, `json-ld`, or `html-heuristic` |
| `workplace` | `remote`, `hybrid` or `onsite`; empty when the site does not say (on-site is never guessed) |
| `employment_type` | `full_time`, `part_time`, `contract`, `intern` or `other`, where the platform provides it |
| `city`, `region`, `country` | Structured place (`country` is ISO-2, `region` a full name), empty when not confidently known |
| `geo_confidence` | `feed` when the platform supplied the structure, `parsed` when it was read from the location text |

**How places are read.** Structure supplied by the hiring platform always wins. Free-text locations are parsed only when the reading is unambiguous, using small curated files in `knowitall/data/` (countries, US states and Canadian provinces, ~230 cities with aliases such as NYC and SF, and region groups such as EMEA and DACH). Ambiguous text stays empty rather than guessed: `Paris` (France or Texas?), `Georgia`, a bare `CA` or `DE`, `Portland, ME`. Those jobs are still found by text search.

## Searching and filtering

The app serves `GET /api/jobs` (used by the interface; it needs the per-launch token like every API call). It searches the newest scan of each company, and every parameter is optional:

| Parameter | Meaning |
|---|---|
| `q` | Text search over title, company, location and department. Words are ANDed and match as prefixes; `"quoted phrase"`; `-word` or `NOT word` excludes; `a OR b`. Places expand through the curated files, so `nyc` also finds New York, `germany` finds anything in Germany, `bay area` finds the cities around San Francisco |
| `workplace`, `employment_type`, `country`, `department`, `source`, `domain` | Repeat a parameter to allow several values; the value `unknown` matches jobs where it is not known. `country` accepts names or codes |
| `region_group` | `EMEA`, `APAC`, `LATAM`, `NORAM`, `AMERICAS`, `EU`, `DACH`, `NORDICS`, `UKI`, `ANZ`, `MENA` (matches the countries in the group and locations that say so, like "Remote - EMEA") |
| `posted_within_days`, `new_only` | Only recent postings; only postings never seen before |
| `run_id` | Look at one specific past scan instead of the newest ones |
| `sort`, `order`, `limit`, `offset` | `sort` is `posted` (default), `title`, `company`, `location` or `found`; `limit` up to 500 |
| `facets` | `0` skips the per-filter counts the response otherwise includes |

The database upgrades itself on start (schema versions are recorded in `schema_version`), including filling in the new fields for jobs saved by older versions.

## Building the Windows exe

PyInstaller does not cross-compile, so **build on the machine you will run it on**:

```bat
pip install pyinstaller
pyinstaller knowitall.spec
```

That produces `dist\KnowItAll\KnowItAll.exe`. Double-clicking it starts the server and opens the interface. The console window stays open on purpose — it shows the scraper log and any startup error (the same log is kept in the app-data `logs/` folder). Chrome is optional.

## Known limitations

- **Workday** gives no departments and only day-level dates ("Posted Today"), so those timestamps are anchored to midnight rather than a precise time. Multi-location roles show the primary location plus a count.
- **Eightfold, iCIMS, SuccessFactors, Taleo, Phenom, Jobvite, Workable and BambooHR** are recognised but have no dedicated reader; those sites fall back to page reading and the log says which platform was seen.
- `html-heuristic` rows come from the listing page only, so location and department may be blank beyond the first 50 enriched postings.
- Tailwind and the fonts load from a CDN, so the interface needs internet to look right. Scraping needs internet anyway, but the styling would degrade on an offline machine.

## Project layout

```
ui.py                     desktop app entry point (server + native window)
main.py                   command line entry point
knowitall.spec            PyInstaller build for the exe
ui/                       the interface (index.html, css/, js/, fonts/): see ui/README.md
knowitall/
  server.py               stdlib HTTP server and JSON API
  runner.py               queue, concurrency, Stop, event stream
  browser.py              BrowserPool: the scraper's own hidden headless Chrome
  shell.py                single-instance lock and remembered window geometry
  paths.py                where data, exports and logs live; moves old data
  logsetup.py             rotating log file
  store.py                SQLite history, migrations, FTS5 search and the job query
  search.py               filter-box syntax to FTS5 (AND / NOT / OR / phrases / place expansion)
  geo.py                  location parsing and place expansion from the curated files
  normalize.py            the job schema: workplace, employment type, structured location
  data/                   curated countries, subdivisions, cities and region groups
  fetch.py                HTTP/browser fetching, caching, timeouts
  discovery.py            careers-page discovery
  ats_detect.py           hiring-platform detection
  ats/                    one reader per platform
  generic.py              fallback: job links + schema.org JobPosting
  normalize.py            common job fields, dates, de-duplication
  scraper.py              the pipeline
```
