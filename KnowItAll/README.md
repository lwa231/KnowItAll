# KnowItAll
Web-Scraper built on the [botasaurus](https://github.com/omkarcloud/botasaurus) framework by omkarcloud.

Give it a company's web address and it finds that company's job listings: **title, link, location, remote flag and department**. Results are saved as JSON and CSV.

```
python main.py stripe.com figma.com
```

## How it works

1. **Find the careers page.** It checks the homepage's "Careers" / "Jobs" links and common paths (`/careers`, `/jobs`, …), plus the `careers.` and `jobs.` subdomains. If those turn up nothing, it checks the sitemap.
2. **Detect the hiring platform (ATS).** Most companies host jobs on a hiring platform. KnowItAll recognises these and reads their official public job feeds, which is fast and complete:
   | Platform | Example |
   |---|---|
   | Greenhouse | figma.com, stripe.com, airbnb.com |
   | Lever | palantir.com |
   | Ashby | ramp.com, linear.app |
   | Workday | nvidia.com |
   | SmartRecruiters | — |

   If a careers page doesn't mention its platform, KnowItAll tries the company name as a board name (e.g. `stripe` on Greenhouse). It only accepts that board if the board's title matches the company.
3. **Otherwise, read the page itself.** It collects links that look like job postings (following "View all jobs" and page 2, 3, … links). It then opens up to 50 of them to read the structured job data (schema.org `JobPosting`) that many sites embed.
4. **Chrome fallback.** If a page blocks plain requests or only renders with JavaScript, it is loaded in headless Chrome.

Pages are cached for 12 hours in `cache/`, so re-running is fast. Use `--no-cache` to force fresh downloads.

## Setup

Requires **Python 3.9+**. **Google Chrome** is only needed for the browser fallback; everything else works without it.

### Windows 10 (including a Hyper-V VM)
```bat
git clone https://github.com/lwa231/KnowItAll.git
cd KnowItAll
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
If `python` isn't found, install Python from python.org and tick **"Add python.exe to PATH"**. Alternatively, use `py -m venv venv`.
If PowerShell refuses to run `activate`, run this once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### macOS
```bash
git clone https://github.com/lwa231/KnowItAll.git
cd KnowItAll
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The first run downloads a small helper binary used by botasaurus' HTTP client ("Downloading @request dependencies…"). This is normal.

## Usage

```bash
python main.py <web address> [<web address> ...] [options]
```

| Option | What it does |
|---|---|
| `--no-cache` | Re-download everything instead of using cached pages |
| `--max-jobs N` | Cap per company for huge boards (default 2000; NVIDIA has about 2000) |
| `--max-enrich N` | Job pages to open for details in step 3 (default 50) |
| `--no-browser` | Never use Chrome |
| `--headful` | Show the Chrome window when the fallback runs (for debugging) |
| `--parallel N` | Scrape N companies at the same time |
| `--show N` | Jobs to print per company (default 10) |

Examples:
```bash
python main.py stripe.com
python main.py https://www.nvidia.com --max-jobs 200
python main.py figma.com ramp.com linear.app --parallel 3
```

## Output

For each company, two files are written to `output/`: `jobs_<domain>.json` and `jobs_<domain>.csv`. The CSV opens directly in Excel.

| Field | Meaning |
|---|---|
| `company` | Company name |
| `title` | Job title |
| `url` | Link to the posting |
| `location` | Location(s), separated by `\|` when there are several |
| `remote` | `True` = fully remote, `False` = on-site or hybrid, empty = unknown |
| `department` | Department or team, when the site provides it |
| `source` | Where the data came from: `greenhouse`, `lever`, `ashby`, `workday`, `smartrecruiters`, `json-ld` (structured data on the job page), or `html-heuristic` (title and link read from the listing page only) |

## Known limitations

- **Workday** doesn't include departments in its job list. For multi-location jobs it shows the primary location plus a count, e.g. `US CA Santa Clara (+3 more)`.
- **Eightfold, iCIMS, SuccessFactors, Taleo, Phenom, Jobvite, Workable and BambooHR** are recognised but have no dedicated reader yet. For those sites, KnowItAll falls back to reading the page, and the summary says which platform it saw.
- `html-heuristic` rows come from the listing page only. Their location and department may be blank, and on unusual sites a few non-job links can slip in.

## Project layout

```
main.py                   command-line entry point
knowitall/
  fetch.py                botasaurus @request / @browser fetching, caching, timeouts
  discovery.py            careers-page discovery
  ats_detect.py           hiring-platform detection (URL patterns)
  ats/                    one reader per platform (greenhouse, lever, ashby, smartrecruiters, workday)
  generic.py              fallback: job links + schema.org JobPosting
  normalize.py            common job fields, remote detection, de-duplication
  scraper.py              pipeline + botasaurus @task that writes output/
```
