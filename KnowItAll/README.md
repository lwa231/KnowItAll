# KnowItAll
Web-Scraper built on the [botasaurus](https://github.com/omkarcloud/botasaurus) framework by omkarcloud.

Give it a company's web address and it finds that company's job listings — **title, link, location, department, posted date**, and whether the posting is one you have never seen before. Results stream into a desktop interface and are saved as JSON and CSV.

There are two ways to run it: the **desktop UI** (`python ui.py`, or the packaged exe) and the **command line** (`python main.py stripe.com`). Both use the same scraper.

## Setup

Requires **Python 3.9+**. **Google Chrome** is required for the UI (the interface itself runs in it) and optional for the command line, where it is only a fallback for JavaScript-heavy sites.

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

`python ui.py` starts a local server and opens **one Chrome window** on it. That same window is what the scraper uses when a page needs JavaScript: the interface stays in tab 1 and scraping happens in sibling tabs that close when they are done. Close the window and everything shuts down.

**Running a scan.** Type one or more addresses into the box (space or comma separated) and press **Start**, or **+ Queue** to line them up without starting. Up to 4 companies scrape at once. **Stop** halts the run and keeps every row already found.

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

Pages are cached for 12 hours in `cache/`.

## Output

Each company writes `output/jobs_<domain>.json` and `.csv` (the CSV opens directly in Excel). The Output view lists what has been written, opens the folder, and exports the whole queue as one file.

| Field | Meaning |
|---|---|
| `company` | Company name |
| `title` | Job title |
| `url` | Link to the posting |
| `location` | Location(s), separated by `\|` |
| `remote` | `True` fully remote, `False` on-site or hybrid, empty unknown |
| `department` | Department or team where the site provides it |
| `posted` | When the posting went up (ISO 8601) |
| `source` | `greenhouse`, `lever`, `ashby`, `workday`, `smartrecruiters`, `json-ld`, or `html-heuristic` |

## Building the Windows exe

PyInstaller does not cross-compile, so **build on the machine you will run it on**:

```bat
pip install pyinstaller
pyinstaller knowitall.spec
```

That produces `dist\KnowItAll\KnowItAll.exe`. Double-clicking it starts the server and opens the interface. The console window stays open on purpose — it shows the scraper log and any startup error. Chrome must be installed.

## Known limitations

- **Workday** gives no departments and only day-level dates ("Posted Today"), so those timestamps are anchored to midnight rather than a precise time. Multi-location roles show the primary location plus a count.
- **Eightfold, iCIMS, SuccessFactors, Taleo, Phenom, Jobvite, Workable and BambooHR** are recognised but have no dedicated reader; those sites fall back to page reading and the log says which platform was seen.
- `html-heuristic` rows come from the listing page only, so location and department may be blank beyond the first 50 enriched postings.
- Tailwind and the fonts load from a CDN, so the interface needs internet to look right. Scraping needs internet anyway, but the styling would degrade on an offline machine.

## Project layout

```
ui.py                     desktop app entry point (server + Chrome)
main.py                   command line entry point
knowitall.spec            PyInstaller build for the exe
ui/index.html             the interface
knowitall/
  server.py               stdlib HTTP server and JSON API
  runner.py               queue, concurrency, Stop, event stream
  browser.py              the shared Chrome (UI tab + scraping tabs)
  store.py                SQLite history and the "seen" hash map
  fetch.py                HTTP/browser fetching, caching, timeouts
  discovery.py            careers-page discovery
  ats_detect.py           hiring-platform detection
  ats/                    one reader per platform
  generic.py              fallback: job links + schema.org JobPosting
  normalize.py            common job fields, dates, de-duplication
  scraper.py              the pipeline
```
