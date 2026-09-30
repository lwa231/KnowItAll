# KnowItAll v0.1 — Implementation Plan

**For:** Claude Sonnet 5.5, working in the KnowItAll repository
**Written:** 2026-09-30, from the owner's audit plus a code investigation
**Baseline:** `github.com/lwa231/KnowItAll` at commit `bc98cf1` ("KnowItAll Version 0.0")
**Bundled with this plan:** `assets/` (logo, app icons, source images). See Appendix A.

---

## 0. Read this first

### 0.1 How to work

1. **Check the baseline.** Run `git log --oneline -3`. If `HEAD` is newer than `bc98cf1`, read the new commits before you start. Tell the owner about any conflict with this plan, then reconcile. Line numbers below refer to `bc98cf1`.
2. **Use your skills.**
   - Start by listing the skills available to you.
   - The owner has given you a **UI/UX skill**. Read its `SKILL.md` before any work item that changes the interface. That covers Phases 1H, 2, 3, 4, 5 and 6: markup, CSS, JavaScript views, copy, states, color. Follow it for layout, hierarchy, copy, empty/loading/error states and accessibility.
   - Use any other skill that fits a task (for example testing, frontend or design-system skills). When a skill and this plan disagree on a visual detail, the skill wins. When they disagree on behavior, this plan wins; tell the owner.
3. **Work on a branch.** Create `v0.1`. Make **one commit per work item** (for example `1C: never cache DNS failures`). Do not push or open a PR unless the owner asks.
4. **Keep the suite green.** Run `pytest` before every commit; all 645 existing tests must still pass. Add the tests listed under each item. The UI has its own browser harness at `ui/tests/run.html`; extend it for UI logic.
5. **Environment note.** botasaurus downloads a helper library and browser version lists from GitHub the first time it is imported. The owner's venv already has them. Do not "fix" import errors caused by a missing network in a sandbox by changing botasaurus.
6. **Ask, don't guess.** If a decision isn't covered in Section 1 or in an item, stop and ask the owner.
7. **When you finish:** run the checklist in Section 4. Then report what changed, what didn't (with reasons), and what the owner must check on their own machine (live sites, Windows taskbar).

### 0.2 Order of work

| Phase | Covers owner item | Why this order |
|---|---|---|
| 1. Scan reliability and honest outcomes | 1.1 | Core function; later phases show its outcomes |
| 2. Parallel scans, Auto/Manual, fast Stop | 1.4 | Rewrites the runner that Phase 1 touches |
| 3. Filters as barriers | 1.2 | Needs Phase 2's counts and states |
| 4. Header cleanup | 1.3 | Frees the header space Phase 2 uses |
| 5. Sessions, history, backups, cache, output | 2.1–2.4 | Needs a schema migration; builds on filters |
| 6. Visual identity: red, logo, app icon | 3.1–3.2 | Independent; can go earlier if blocked elsewhere |
| 7. Carry-over fixes | (audit v0.0) | Small fixes in the same code paths |

---

## 1. Decisions already made with the owner

These were settled in Q&A. Don't re-open them.

| Topic | Decision |
|---|---|
| **Filters (1.2)** | **Save everything, show only matches.** Every posting still goes into the database, so new/gone/closed tracking stays correct. The owner's filters are set *before* scanning, persist across launches, and hide non-matching postings **everywhere**: live feed during a scan, old scans, counts, and exports. No scan-time skipping. |
| **Parallel (1.4)** | A **1 \| 2 \| 3** control replaces `+ Queue` in the header, in both modes. **Auto (default):** pressing Enter on an address starts it immediately if a slot is free; otherwise it waits and starts by itself when a slot frees. **Manual:** Enter only adds addresses to the list; **Start** runs the list N at a time. The Auto/Manual switch and the default N live in Settings. Maximum is 3. |
| **History (2.1)** | History shows **only the current session's scans** (session = one app launch until quit). On every launch the database is **backed up automatically** into the backups folder (keep the last 10 automatic backups). Postings stay in the live database so "new" and "closed" keep working. Add a note at the bottom of History: **"New history features coming soon."** |
| **Output (2.4)** | Output stays as it is, plus it becomes compatible with **all three** new pieces: **filters** (exports contain matching postings by default), **session history** ("Export this session"), and **backups** (Output lists backups and has Back up now / Open folder). |
| **Header toggles (1.3)** | The owner allowed "make them useful or drop them". **Drop them from the header** (Fresh data, Max jobs, Detail depth) and keep them in Settings with plain explanations. Reasoning is in Phase 4. |
| **Color (3.1)** | Every purple/blue/lavender becomes the brand red **`#bf1704`** (191, 23, 4). Keep the current dark/neutral surfaces and the grey text colors. Exact accessible values are in Appendix B. |
| **Logo and icon (3.2)** | The "KiA" image is the in-app logo. The small square glyph image is the app icon, re-rendered at high resolution. Both are provided in `assets/` (Appendix A). |

---

## 2. Root causes already found

The owner reported symptoms. These are the causes, confirmed by reading the code and running scripts against it. You don't need to rediscover them. Fix them.

| # | Symptom (owner's words) | Cause | Where |
|---|---|---|---|
| R1 | Chase: "no listings… should say so instead of freezing" | Homepage links to other domains are thrown away (`continue  # off-site ATS links…`). chase.com's only careers link is `https://careers.jpmorgan.com/US/en/chase`, so it's dropped. A careers probe that *redirects* to another domain is also dropped ("redirected somewhere unrelated"). | `discovery.py:95`, `discovery.py:212` |
| R2 | Tesla: feed stays empty | Tesla job URLs look like `/careers/search/job/<slug>-<id>`. `JOB_PATH_RE.search()` only examines the **first** match, `/careers/search`. `search` is in `NAV_SEGMENTS`, so the link is rejected. Test result: 0 of 5 Tesla-shaped links recognized. The careers page is also an empty JavaScript shell, and Tesla uses bot protection. | `generic.py:149` |
| R3 | Uber: feed stays empty | `LOCALE_RE` reads `/us/en/careers/` as the language "us", not English, and subtracts 40 from the page score. The listing (`/global/en/careers/list/`) is rendered by JavaScript from an internal JSON API and uses a "show more" button instead of `?page=` links. | `discovery.py:124`; `generic.pagination_urls` |
| R4 | "Freezes", "stale" feed | (a) The empty state in a company pane checks the **global** `state.running`, so a company that finished with 0 postings keeps saying "Scanning…" while any other company runs (`table.js:109`). (b) The reason a scan found nothing is stored in `company["notes"]` but **no UI code ever displays notes**. (c) A company has no time limit: each fetch can take up to 60 s plus a 60 s fallback, each Chrome render up to 90 s (`browser.py` `LOAD_TIMEOUT=45` ×2), and the sitemap lookup up to 90 s. A blocked site can run for many minutes with nothing on screen. (d) With the default of one scraping browser, renders from all companies queue behind each other. | `table.js:109`, `runner.py`, `fetch.py:40`, `browser.py:18`, `discovery.py:137` |
| R5 | "Error states" that stick | A DNS failure is **cached on disk for 12 hours**: `_fetch_page` returns `error="host does not resolve"` without `DontCache`, and so does `_fetch_json`. `host_resolves()` also has an `lru_cache` that lasts the whole session. One Wi-Fi blip makes a company "unreachable" until the cache expires or the app restarts. | `fetch.py:68`, `fetch.py:162`, `fetch.py:178` |
| R6 | Filters "don't filter"; "junk dump" | (a) Filter chips are built only from facet values **already in the database**, so before a company is scanned there's nothing to pick ("Nothing to choose from yet"). (b) Header "Jobs found", queue counts and exports ignore filters. (c) The **All** feed shows the newest scan of **every company ever scanned**, not just this session's (`store._scope`, `LIVE_STATUSES`). | `filterbar.js options()`, `run.js paintMetrics`, `store.py:574-582` |
| R7 | Header toggles "do nothing" | They only change options for the **next** Start. Max jobs only matters for Workday/SmartRecruiters boards above the cap. Detail depth only matters for company-built career sites. Fresh data only bypasses the 12-hour cache. None of them changes what's on screen. | `run.js:80-82`, `settings.js` |
| R8 | Stop takes >5 s; queue waits | (a) Pressing **Enter in the address box during a scan calls `toggle()`, which *stops the scan*** (`run.js:20,66`). The typed address stays in the box. (b) Stop only sets a cancel flag. The UI flips back only when every worker thread has returned, and in-flight HTTP requests (up to 60 s + 60 s), Chrome renders (up to 90 s) and fetch batches of 8 don't check the flag. (c) `+ Queue` during a run adds a company that is never scheduled until the whole run ends and Start is pressed again (`runner.py:108`). | `run.js`, `runner.py:129-206`, `fetch.py:103`, `browser.py:177` |
| R9 | History "saves far too much" | History lists up to 200 runs across all time. There's no concept of a session. | `store.history`, `views/history.js` |
| R10 | Cache "does nothing" | Clearing the cache deletes `DATA_DIR/cache/*`, which only affects the **next** scan (the feed reads the database). It also makes the next scan **slower**, not faster: the cache is what makes rescans within 12 h fast. The in-memory DNS cache isn't cleared. Startup prune keeps cache files for 7 days even though they expire after 12 h. | `maintenance.py`, `fetch.py:41` |
| R11 | Purple/blue UI | Every accent comes from the tokens `--accent #35365e`, `--accent-hover`, `--accent-text #9899c8`, `--glow`, `--focus` in `ui/css/tokens.css` (both themes). The pixel mascot, chips, switches, segmented controls, job-title links, progress bar and nav highlight all use them. | `ui/css/tokens.css` |
| R12 | Python icon in taskbar/dock | `ui.py:160` calls `webview.start()` without `icon=`. pywebview 6.2.1 then extracts the icon from `sys.executable` on Windows (the Python icon) and leaves the Python rocket on macOS. The PyInstaller spec has no `icon=` and no macOS `BUNDLE`. The sidebar shows a `LOGO` placeholder (`index.html:39`). | `ui.py`, `knowitall.spec`, `ui/index.html:39` |

---

## 3. Work items

Each item lists **what the owner should see**, **how to build it**, and **tests** (definition of done).

### Phase 1 — Scan reliability and honest outcomes (owner item 1.1)

#### 1A. Every company scan ends in one explicit outcome

**Owner-visible:** a company never stays "scanning" forever. It ends in exactly one outcome, and the message is shown in its pane and its queue row.

| `outcome` | When | Message (Appendix C has the full copy) |
|---|---|---|
| `found` | ≥1 posting | "{n} postings" |
| `no_listings` | A careers page was found but no postings could be read | **"No listings available on this page."** plus a link to open the careers page |
| `no_careers_page` | No careers page was found at all | "No careers page found on {domain}." |
| `unreachable` | DNS or connection failure on the homepage | "Couldn't reach {domain}. Check the address or your connection." |
| `blocked` | The site refused automated access (403/429/503, or a challenge page, even in Chrome) | "{domain} blocked automated access." plus "Open careers page" |
| `unsupported` | Careers are on a platform with no reader (iCIMS, Taleo, Oracle, Eightfold, Phenom, SuccessFactors, Workable, Jobvite, BambooHR) | "{domain} uses {platform}, which KnowItAll can't read yet." plus "Open careers page" |
| `timed_out` | The per-company time limit was reached (1B) | "Stopped after {limit}: this site is slow. {n} postings found." |
| `stopped` | The user pressed Stop | "Stopped. {n} postings found." |
| `error` | Unexpected exception | "Something went wrong scanning {domain}." (details in the log) |

**Build:**
- In `scraper.find_jobs`, compute `result["outcome"]`, `result["outcome_detail"]` (the message) and `result["careers_url"]`. The data is already there: `unreachable(home)`, `candidates`, `unsupported`, `jobs`, and page statuses.
- Add challenge-page detection in `fetch.py`, e.g. `is_challenge(page)`. Title or body markers to look for: `Access Denied`, `Just a moment...`, `Attention Required! | Cloudflare`, `errors.edgesuite.net`, `Reference #`, `_Incapsula_Resource`, `px-captcha`, `captcha`. A 200 response carrying a challenge counts as blocked.
- `runner._scrape_one` stores `outcome`, `outcome_detail` and `careers_url` in the company state (and in the run row after the Phase 5 migration). An exception sets `error`.
- `normalize.make_job` and the UI must accept only `http`/`https` URLs for `careers_url` and job URLs (see 7B).

**Tests:** `tests/test_outcomes.py` — use fixtures for each outcome: unreachable home, 403 home with a challenge body, no candidates, unsupported detection, candidate with 0 jobs, jobs found, and exception → error.

#### 1B. Per-company time limit and visible progress phases

**Owner-visible:**
- Each scanning company shows what it's doing ("Finding careers page…", "Reading Greenhouse board…", "Loading page in Chrome…", "Reading job pages 20/50…").
- No company runs longer than the limit (default **3 minutes**). The limit is a Settings option: 1 / 3 / 5 minutes.

**Build:**
- `context.CancelToken` gains a `reason` (`"stop" | "time_limit"`). Add `ScanContext.phase(text)`, which emits an event `{"kind": "phase", "domain", "text"}`; the runner stores it in the company state.
- The runner starts a timer per company that cancels that company's token with reason `time_limit`. Whatever was found is kept, and the outcome becomes `timed_out`.
- Call `ctx.phase()` at the start of each pipeline stage in `scraper.find_jobs`, `discovery.discover`, each ATS reader and `generic.extract_jobs`.
- Shorten the waits that can't be cancelled (Phase 2C makes them cancellable too):
  - sitemap lookup: 90 s → 30 s;
  - `DEADLINE` per fetch: 60 s → 30 s;
  - don't fall back to plain `requests` after a botasaurus **timeout** (only after errors).

**Tests:** a fake reader that sleeps longer than a 1-second limit → outcome `timed_out`, and rows emitted before the cutoff are kept. Phase events arrive in order.

#### 1C. Never cache failures; DNS cache with a short lifetime

**Build:**
- In `fetch._fetch_page` and `_fetch_json`, wrap the `host does not resolve` result in `DontCache` (`fetch.py:162`, `:178`).
- Replace `@lru_cache` on `host_resolves` with a small TTL cache: positive answers 10 min, negative answers 60 s. Retry a failed lookup once after 1 s before declaring the host dead.
- `Runner.start/submit` clears negative DNS entries.
- `maintenance.clear_cache()` clears the DNS cache as well (see 5D).

**Tests:** a DNS failure followed by a success within the same session → the second scan reaches the site. Cached-file count doesn't grow on DNS failures.

#### 1D. Follow careers links to parent or sister domains

**Owner-visible:** entering `chase.com` finds JPMorgan Chase's careers site. The company row says "Careers for chase.com are hosted on careers.jpmorgan.com."

**Build:**
- `discovery.homepage_links`: keep off-domain links with a careers score ≥ 20 (text "Careers"/"Jobs"/"Join us", or a careers-style path).
- Ignore job boards and social sites with a denylist: linkedin, indeed, glassdoor, ziprecruiter, monster, facebook, instagram, x.com/twitter, youtube, tiktok.
  - If the *only* careers link goes to LinkedIn/Indeed, end with `no_listings` and the detail "{domain} lists its jobs on LinkedIn/Indeed."
- `_collect`: accept a page whose final host is another domain when the request was a careers link or careers probe **and** the page looks like a careers page (title/path).
- Add `Site.careers_domains` (a set of registrable domains, starting with `site.domain`). Use it wherever `same_site(..., site.domain)` filters job links (`generic._is_job_link`, `scraper._guess_boards`).
- Record the careers host so the UI can show the note above.

**Tests:** a fixture of the chase.com homepage whose only careers link is `careers.jpmorgan.com/US/en/chase` → that page becomes a candidate, and job links on it are accepted. A LinkedIn-only fixture → the LinkedIn message.

#### 1E. Discovery and link-detection fixes (Tesla, Uber shapes)

**Build:**
- `generic._is_job_link`: iterate over **all** `JOB_PATH_RE` matches (`finditer`). The link is a job link if any match's segment is not navigation **and** that segment isn't the last path segment of a listing URL. Required results:
  - `/careers/search/job/software-engineer-225712` → job
  - `/careers/search/` → not a job
  - `/global/en/careers/list/135592/` → job
  - `/global/en/careers/list/?location=…` → not a job (query-only variant of the listing)
- `discovery._final_score`: treat a two-segment prefix `/<country>/<lang>/` (e.g. `/us/en/`, `/gb/en/`) as English when the **language** segment is `en`. Apply the −40 only for non-English languages. Let `EXACT_CAREERS_PATH_RE` accept one or two locale segments.

**Tests:** extend `tests/test_scan_pipeline.py` with the three URL families above. `/us/en/careers/` must now score at least as high as `/careers/` minus the per-segment cost.

#### 1F. JavaScript-rendered listings: wait, scroll, "load more", and read the JSON the page loads

**Owner-visible:** career sites that load jobs with JavaScript (Uber-style) return postings, or end with an honest `blocked` / `no_listings` within the time limit.

**Build:**
- Add a listing mode to `BrowserPool.render(url, should_cancel, mode="page"|"listing")`. In listing mode:
  1. Before navigating, register `driver.after_response_received(...)` and collect JSON responses: same registrable domain or a known API host, `content-type` JSON, ≤ 2 MB each, at most 30.
  2. After the document is ready, poll every 500 ms (up to 10 s) until ≥3 job-like links exist. Use a callback from `generic` so the rule stays in one place.
  3. Scroll to the bottom up to 5 times while new links keep appearing.
  4. Click buttons or links matching `/(load|show|see|view) more|more jobs|more results/i`, up to 10 times, stopping at `max_jobs`.
  5. Return `html`, `final_url` and `json_payloads` in the page dict.
  6. Check `should_cancel` between every step.
- New module `knowitall/json_jobs.py`: find arrays of objects where ≥3 items have a title-like key (`title`, `name`, `jobTitle`, `text`, `position`) and an id/url-like key (`id`, `jobId`, `reqId`, `requisitionId`, `url`, `applyUrl`, `hostedUrl`, `slug`, `externalPath`).
  - Map the location-, department- and date-like keys it can find to `make_job`.
  - If there's no URL, build one from the careers page URL and the id (`…#job-<id>`) so postings stay distinct (see 7A).
  - Use `source="json-sniffed"`.
- In `scraper.find_jobs`, step 4: when rendered HTML gives no jobs, try `json_jobs.extract(payloads)`.
- **Per-site adapters.** Some sites use abbreviated keys that the generic reader can't map; Tesla's careers state API is suspected to. Add `knowitall/adapters/` with a registry keyed by domain, and add an adapter **only after you've inspected a real payload on the owner's machine**. With `--debug`, log the URL, top-level keys and array lengths of each captured payload.
- Be honest about limits: Tesla uses bot protection that may block headless Chrome regardless. The acceptance bar is postings **or** outcome `blocked` within the time limit, never an endless scan.

**Tests:** `tests/test_json_jobs.py` with fixtures shaped like an Uber-style search result (`results[]` with `id`, `title`, `location{city,country}`, `department`) and a nested-state shape. Also test the pool's listing mode with a fake driver: wait, scroll, click-more loop, and a cancel between steps.

#### 1G. (Stretch — ask the owner before starting) Oracle Recruiting Cloud reader

`careers.jpmorgan.com` runs on Oracle Recruiting Cloud (`*.fa.*.oraclecloud.com`). Oracle is also common at other large employers. Oracle Recruiting Cloud appears to expose a public REST endpoint (`/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber=…`). Before writing the reader:
1. Confirm the exact request and response shape with a live call on the owner's machine.
2. Record a fixture from that response.
3. Then write `ats/oracle.py` plus a detection pattern.

Until then, 1A's `unsupported` message covers Oracle.

#### 1H. Show outcomes and progress in the UI

**Owner-visible:** you can always tell what each company is doing or why it has nothing.

**Build (use the UI/UX skill):**
- `table.js emptyHTML`: decide from the **company's** state (`byDomain(pane.key)`), never the global `state.running`.
  - scanning → the phase text and elapsed time;
  - finished with 0 → the outcome message, plus "Open careers page" when `careers_url` exists.
- **All** pane: below the table (or as the whole body when empty), a compact list "Companies with no listings", one line each with the outcome message.
- Queue row (`queue.js`): outcome glyph plus a one-line reason under the name, e.g. "blocked", "no careers page", "3 min limit".
- Links open in the system browser. Keep `http`/`https` only.
- The queue must not rebuild its HTML on every server message; update rows in place so keyboard focus isn't lost (audit L6).

**Tests:** UI harness cases for each outcome, and for "company A done with 0 while company B scans" → A shows its outcome, not "Scanning…".

---

### Phase 2 — Parallel scans, Auto/Manual, fast Stop (owner item 1.4)

#### 2A. A runner that admits work while it runs

**Build:**
- Replace "one `ThreadPoolExecutor` per Start" with a long-lived scheduler in `runner.py`:
  - an executor with 3 workers, gated by `self.parallel` (1–3);
  - `submit(urls, start: bool)`;
  - `_fill_slots()`, called on submit, on completion, and on parallel change.
- New company states:
  - `ready` — Manual mode: added but not started;
  - `waiting` — Auto mode: no free slot yet; show its position in line;
  - `scanning`, then an outcome from 1A.
- `running` = any company scanning or waiting.
- Changing `parallel` takes effect immediately: raising it starts waiting companies; lowering it lets running ones finish and stops new starts.
- Submitting an address that's already scanning or waiting is ignored with a toast "Already scanning {domain}". Submitting a finished one rescans it.
- API:
  - `POST /api/scan {urls}` — Auto: add and start. Manual: add as `ready`.
  - `POST /api/start` — Manual: start all `ready`.
  - `POST /api/parallel {n}` — saves the setting and applies it live.
  - `POST /api/stop {domain?}`.
  - Keep `/api/run` and `/api/queue` as thin aliases so older clients and the CLI keep working, or update the CLI (`main.py`) to use `submit` + `wait`.
- Fix the race from audit M9e while you're here: `_drive`/finish logic must not touch shared pool or idle state after releasing the lock.

**Tests:**
- Auto: submit A, B, C, D with parallel=2 → A and B scanning, C and D waiting; when A finishes, C starts.
- Change parallel 2→3 → D starts at once.
- Manual: submit → `ready`, nothing runs until `/api/start`.
- A duplicate submit is ignored.

#### 2B. Settings and header controls

**Build (use the UI/UX skill):**
- `settings.py`:
  - add `parallel_mode: "auto" | "manual"` (default `"auto"`);
  - change `concurrency` limits to 1–3, default 3; clamp stored 4 → 3 on load. Rename the setting to `parallel` if you like, but migrate the old key.
- Header (`index.html` first controls row):
  - address input;
  - a segmented **1 | 2 | 3** control labelled "At once" (tooltip: "How many companies scan at the same time"). It replaces `+ Queue` and mirrors the setting;
  - primary button: Auto shows **Scan**; Manual shows **Add**, plus a separate **Start (N ready)**;
  - a separate **Stop** button, visible only while something is scanning or waiting. Style it so it doesn't look like the primary action (see 6A on red).
- **Enter in the address box never stops anything.** Auto: Enter = Scan. Manual: Enter = Add. Always clear the box after a successful submit.
- Accept one or several addresses in one submit (space/comma separated), as today.
- Settings view → new "Scanning" panel (see Phase 4): "Parallel scans: Auto / Manual" with help text, and "Default at once: 1 | 2 | 3".
- Browser workers: default the scraping-browser count to `min(parallel, 2)` so three companies don't all wait on one Chrome. Keep the System view control.

#### 2C. Stop that responds instantly

**Owner-visible:**
- The button changes to "Stopping…" at once and ignores repeat clicks.
- Within **1 second** every affected company shows `stopped` with what it found, and the header shows idle (unless other companies are still running after a per-company stop).

**Build:**
- `Runner.stop(domain=None)` cancels the tokens, then **immediately finalizes** each affected company:
  - state/outcome `stopped`;
  - `store.finish_run(..., "stopped", complete=False)`;
  - emits `companies`/`status`.
  - Worker threads may keep running in the background; everything they produce afterwards is discarded. `on_jobs` checks the token first, and the finish path skips a company that's already finalized.
- Make every blocking wait cancellable:
  - `fetch._run_with_deadline(fn, seconds, should_cancel)` joins in 0.2 s slices and abandons the thread on cancel;
  - `fetch_pages` batch size 8 → 4 and checks between items where possible;
  - `BrowserPool._acquire/_load` check `should_cancel` every 250 ms;
  - the sitemap lookup gets the same cancellable wrapper.
- UI: disable the Stop control for 1 s after a click, and show "Stopping…" until the state arrives.

**Tests:** with a fake `find_jobs` that blocks for 30 s, `stop()` → company `stopped` and `runner.running` false within 1 s. Rows emitted after the stop are not saved. Per-company stop leaves the others running.

---

### Phase 3 — Filters as barriers (owner item 1.2)

Decision: **save everything, show only matches**, consistently everywhere.

#### 3A. A persistent filter profile you can set before scanning

**Build:**
- Store the active filters in `settings.json` under `filters`, validated with a subset of `store.normalize_filters`. Load them at boot. Save them from the UI (debounced) through `POST /api/filters`. They survive restarts.
- Chips offer the **full vocabulary**, not just values already in the database:
  - Workplace: remote / hybrid / on-site / not stated.
  - Type: full-time / part-time / contract / intern / other / not stated.
  - Posted: any / 24 h / 7 d / 30 d / 90 d.
  - Region: all region groups, plus a type-ahead over **all countries**. Add `GET /api/geo/countries` from `knowitall/data/countries.json`.
  - Department: free text plus suggestions from facets.
  - Source: static list.
- Facet counts are shown where known. A choice with 0 is still selectable (label it "0 so far").
- **Not stated:** each chip gets a checkbox "Include postings that don't say", default **off**. When postings are hidden because a field is unknown, show a line in the feed footer: "+{n} postings don't state workplace — show them" (one click turns the checkbox on).

#### 3B. Filters apply everywhere

**Owner-visible:**
- The live feed during a scan, old scans, per-company panes, counts and exports all obey the filters.
- While filters are on, a banner under the filter bar reads: "Filters on: Remote · United States · last 7 days — showing 23 of 1,204. [Edit] [Clear]".

**Build:**
- Feed panes already query `/api/jobs` with the filters. Keep that. Also:
  - Queue rows show **matches/total** while filters are on (e.g. "12 / 340"). Use the `domain` facet from a filtered `/api/jobs?facets=1&limit=1` call, throttled like `refreshFacetsLive`.
  - The header metric "Jobs found" becomes "Matching" (with the total in smaller text while filters are on).
  - New-count badges and announcements count matches.
  - During a scan with filters on and 0 matches so far: "Scanning… 0 matches so far ({n} postings checked)".
- Exports honor filters (Phase 5E).

**Tests:**
- API: saved filters round-trip through `/api/filters` and survive `Service` re-creation.
- UI harness: with an empty database the Workplace chip still offers all choices; selecting "Remote" before a scan → rows arriving later are filtered; the queue count shows matches/total.

---

### Phase 4 — Header cleanup (owner item 1.3)

**Reasoning (share with the owner in your summary):** Fresh data, Max jobs and Detail depth only change how the *next* scan fetches pages. They never change what's on screen, and two of them only matter for certain site types. That's why they felt broken.

**Build (use the UI/UX skill):**
- Remove the Fresh data switch and the Max jobs and Detail depth inputs from the header (`index.html` second controls row; `run.js:80-82` bindings).
- The header's second row keeps the metrics: Matching, Elapsed, Progress. The 1 | 2 | 3 control from 2B sits beside the address input.
- Settings → **Scanning** panel, with plain-language help:
  - "Parallel scans: Auto / Manual", and "Default at once: 1 | 2 | 3" (from 2B).
  - "Time limit per company: 1 / 3 / 5 min" (from 1B).
  - "Max postings per company — only matters for very large boards (Workday, SmartRecruiters). Default 2,000."
  - "Job pages opened for details — company-built career sites only; more is slower but fills in location and department. Default 50."
  - "Reuse downloaded pages for: Off / 1 hour / 12 hours" (replaces "Fresh data"; see 5D).
- Progress: compute it from companies finished/total **in this session**. Don't let a waiting company read as 0%.

---

### Phase 5 — Sessions, history, backups, cache, output (owner items 2.1–2.4)

#### 5A. Sessions (schema migration v4)

**Build:**
- Migration 4 in `store.py`:
  - table `sessions(id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, app_version TEXT)`;
  - columns `runs.session_id TEXT`, `runs.outcome TEXT`, `runs.outcome_detail TEXT`;
  - index on `runs(session_id)`.
- Follow the existing pattern: backup first, one transaction.
- **Fix the multi-process race (audit H6):** take `BEGIN IMMEDIATE` *before* reading `schema_version`, re-read it inside the transaction, and skip migrations that are already applied.
- `Service` creates a session at launch (`ui.py` and `main.py`) and closes it on quit.
- At startup, mark runs still `running` from an earlier session as `interrupted` (audit H5b).
- `/api/jobs` gains `session=current|all` (default `current` for the UI):
  - scope = the newest run per domain **among this session's runs**;
  - old sessions' postings stay in the database (needed for new/gone/closed) but aren't shown in the feed.
- Gone/Closed views are scoped to companies scanned this session.

**Tests:**
- The migration from a v3 database keeps all data.
- Two processes calling `store.init()` on the same v3 database both succeed.
- An orphaned `running` run becomes `interrupted`.
- The feed after a restart shows no companies until one is scanned this session.

#### 5B. History page

**Build (use the UI/UX skill):**
- `/api/history?session=current` lists this session's runs, newest first, with outcome, source, jobs, matches (when filters are on) and time. Clicking a run opens it, as today.
- Empty state: "No scans yet this session."
- Footer note (owner's request): **"New history features coming soon."** Add a second line: "Earlier sessions are saved in backups (Settings → Data & storage)."

#### 5C. Backups

**Owner-visible:** Settings → **Data & storage** has "Back up now", a list of backups (date, size, automatic/manual) and "Open backups folder". Backups live in the app-data folder, so they survive an uninstall and reinstall.

**Build:**
- Add `paths.BACKUPS_DIR = DATA_DIR / "backups"`.
- `store.backup_to(path)` uses the sqlite3 **backup API**, which gives a consistent copy while in WAL mode. Never copy the file directly.
- `Service.create_backup(kind)` writes `knowitall-YYYY-MM-DD_HHMMSS-{auto|manual}.db` plus a copy of `settings.json` alongside (`…-settings.json`).
- **Automatic backup on every launch**, before migrations run: keep the last **10** automatic backups and delete older automatic ones. Manual backups are never deleted automatically. Add a Settings switch "Back up automatically at launch" (default on).
- API:
  - `GET /api/backups` → `[{name, kind, bytes, created}]`;
  - `POST /api/backups` (create manual);
  - `POST /api/backups/open-folder`.
- Restore is **out of scope**. Mention it as a possible next step in your summary.
- Installer note (if you touch packaging): the uninstaller must **not** delete `%LOCALAPPDATA%\KnowItAll` or `~/Library/Application Support/KnowItAll`.

**Tests:** a backup during an open write transaction produces a valid database with the same row counts. Auto-rotation keeps 10. Manual backups survive rotation.

#### 5D. Cache that makes sense

**What "cache" means in this app** (include a short version in the Settings help text):

| Thing | Where | What it does | What clearing it does |
|---|---|---|---|
| Page cache | `DATA_DIR/cache/` (botasaurus, one JSON file per request) | Rescans within the reuse window read saved pages instead of downloading. **This makes rescans faster.** | The next scan of each company downloads everything again (slower, but fresh) |
| DNS cache | memory (`fetch.host_resolves`) | Skips repeated DNS lookups | Forgets failed lookups (see 1C) |
| Parsed-page cache | memory (`fetch._SOUPS`, ≤16 MB) | Avoids re-parsing HTML within a scan | Frees memory |
| **Not cache** | `history.db`, exports, backups | Your data | Never touched by Clear cache |

**Build:**
- Move the Storage panel from the System view into **Settings → Data & storage**, next to Backups (the owner expects them together). System keeps read-only stats.
- **Clear cache** deletes `DATA_DIR/cache/*`, calls `host_resolves` clear and `_SOUPS.clear()`, and shows a toast such as "Cleared 1,284 saved pages (38 MB). The next scan of each company downloads everything fresh." Disable it while scanning, with a tooltip explaining why.
- Replace the fixed 12-hour TTL with the setting "Reuse downloaded pages for: Off / 1 hour / 12 hours".
  - botasaurus sets `expires_in` at decoration time, so define one decorated fetch function per option (a dict of three), or use `cache="REFRESH"` for Off.
  - Startup prune deletes cache files older than the **selected** lifetime, not 7 days.
- Fix R5 (1C) so failures are never cached.

#### 5E. Output compatible with filters, sessions and backups

**Build (use the UI/UX skill):**
- **Filters:** exports contain matching postings by default.
  - Add a Settings → Output option: "Exports include: Matching postings only / Everything" (default matching).
  - Filenames get `_filtered` when filters applied. Write the active filter summary into the JSON export's top-level metadata. For CSV, use a sidecar `.txt` with the same stem; don't add a comment row to the CSV.
- **Sessions:** "Export whole queue" becomes **"Export this session"** → `knowitall_session_YYYY-MM-DD_HHMM[_filtered].csv`. The Output list shows this session's files first; older files stay listed below.
- **Backups:** the Output page gets a "Backups" section: the same list and actions as 5C (reuse the component).
- **Security (audit H7a):** in `export.write_csv`, prefix any cell that starts with `=`, `+`, `-`, `@`, tab or carriage return with `'`.

**Tests:** a filtered export contains only matching rows. A formula-looking title is neutralized. The session export name is correct.

---

### Phase 6 — Visual identity (owner items 3.1, 3.2)

#### 6A. Swap every purple/blue for the brand red

**Build (use the UI/UX skill):**
- Replace the accent tokens in `ui/css/tokens.css` with the values in **Appendix B**, in both themes.
- `#bf1704` is the fill color. As **text** on the dark surfaces it's only 2.1–3.3:1, so dark-theme text/glyph accents use the lighter tint `#fb6655` from the same hue (≥4.5:1 on every dark surface). The light theme uses `#b91604`. `tests/test_ui_contrast.py` must keep passing unchanged.
- Search the whole `ui/` tree for any remaining purple/blue (hex, `rgb()`, `hsl()`, named colors, SVG `fill`/`stroke`). Only the tokens should hold color.
- **Red is now the brand color, and red also means error/danger.** Keep them distinguishable:
  - errors always carry the alert icon plus a text label;
  - the Stop button uses an outlined style, not the filled primary style;
  - "failed" keeps its × glyph.
  - Review `--red` with the UI/UX skill. It may need a different tone from the brand red, e.g. a pinker red, so they don't read as the same thing.
- Consider making job-title links `--text-bright` with a red hover/underline instead of red text, so a long table isn't a wall of red. Let the UI/UX skill decide.
- The pixel mascot and progress bar pick up the new accent automatically through the tokens. Check them in both themes.

#### 6B. In-app logo

**Build:**
- Copy `assets/logo/logo-kia.svg` → `ui/img/logo-kia.svg`.
- Inline it in the sidebar brand block (`index.html:39`, replacing `<div class="logo-slot">LOGO</div>`) so it inherits `color: var(--text-bright)` (the path uses `fill="currentColor"`). It then works in both themes.
- Height ≈ 24–28 px. Keep "KnowItAll" available to screen readers (`aria-label`/`<title>` or visually hidden text).
- Remove the `.logo-slot` placeholder styles. Decide with the UI/UX skill whether the "KnowItAll / Job Listing Scraper" text stays beside the logo.

#### 6C. App icon everywhere (no more Python icon)

**Build:**
- Put the icon files in `knowitall/assets/icons/`: `knowitall.ico`, `knowitall.icns`, `icon-256.png`, `icon-1024.png`, `icon-macos-1024.png`. Add them to the spec's `datas`.
- `ui.py`, before creating the window:
  - **Windows:** `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KnowItAll.Desktop")`, so the taskbar stops grouping the window under Python and uses the window icon.
  - Pass `icon=` to `webview.start(...)`: `.ico` on Windows, `icon-macos-1024.png` on macOS, `icon-256.png` elsewhere. pywebview 6.2.1 applies it on Windows (WinForms `Form.Icon`) and macOS (`NSApp.setApplicationIconImage_`), even though the docstring says GTK/Qt only.
  - Pin `pywebview>=6.2,<7` in `requirements.txt`.
  - **macOS, run from source:** the Dock shows the icon, but the menu bar still says "Python". Optionally set `NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "KnowItAll"` via PyObjC before `webview.start`. The packaged `.app` solves this properly.
- `knowitall.spec`:
  - `EXE(..., icon="knowitall/assets/icons/knowitall.ico")`;
  - on macOS, add `BUNDLE(coll, name="KnowItAll.app", icon="knowitall/assets/icons/knowitall.icns", bundle_identifier="com.knowitall.desktop", info_plist={"CFBundleName": "KnowItAll", "NSHighResolutionCapable": True})`;
  - refresh `hiddenimports` to the current module list.
- The desktop shortcut uses the exe's icon automatically. If you add an installer script (Inno Setup, optional, ask first), give it a "Create desktop shortcut" task and keep app data on uninstall (5C).

**Verify (owner, on real machines):**
- Windows: `python ui.py` and the built exe both show the KiA glyph in the taskbar, Alt-Tab and the title bar; the desktop shortcut shows it.
- macOS: the Dock shows it.

---

### Phase 7 — Carry-over fixes in the same code paths

These come from the v0.0 audit (`KnowItAll_audit_v0.0.txt`). They affect the features above. Do them as separate commits.

- **7A. URL identity (audit H2/H3).** `normalize.url_key` drops query strings, so `?jobid=101` and `?jobid=102` become one posting.
  - Keep id-bearing parameters: `job, jobid, job_id, jid, id, reqid, req_id, requisitionid, gh_jid, career_job_req_id, posting_id` (case-insensitive).
  - Keep the fragment when it's `#job-<id>` (from 1F).
  - Needs a store migration that re-keys postings. Merge duplicates, keeping the oldest `first_seen`.
  - Inline JSON-LD postings without a `url` get `page_url#job-<identifier or slug>`.
- **7B. Link safety (audit H7b).** `make_job` accepts only `http(s)` URLs; `rowHTML` checks again before rendering `href`.
- **7C. Windows blank-window risk (audit H8).** `server._file` uses an explicit MIME map for `.js .mjs .css .html .json .svg .woff2 .png .ico` before falling back to `mimetypes`.
- **7D. Workday failed pages (audit H4a).** A page that fails calls `ctx.mark_truncated(...)`, so the scan isn't "complete" and nothing is marked gone.
- **7E. README.** Update it for everything above: the new header, Settings panels, sessions, backups, the meaning of cache, CLI flags, and project layout.

---

## 4. Final verification checklist

Run everything on the owner's machine (live sites need it).

1. `pytest` — all old and new tests pass. The UI harness (`ui/tests/run.html`) passes.
2. **Smoke script.** Add `tools/smoke_scan.py` that scans a list of domains through `Service` and prints domain, outcome, postings, seconds. Run it with `stripe.com uber.com tesla.com chase.com jpmorganchase.com nvidia.com`. Expected:
   - `stripe.com` → `found`;
   - `nvidia.com` → `found` (Workday);
   - `chase.com` → careers followed to `careers.jpmorgan.com`, then `found` (if 1G is done) or `unsupported (Oracle)` with an "Open careers page" link;
   - `uber.com` → `found` or `blocked`;
   - `tesla.com` → `found` or `blocked`;
   - **every one** finishes within its time limit.
3. **In the app** (dark and light themes):
   - Scan with Enter three times in a row (Auto, 1 | 2 | 3 = 3) → three scan at once. Set 1 | 2 | 3 to 1 → the next one waits and shows its position.
   - Stop mid-scan → "Stopping…" at once, everything stopped within 1 s, nothing added afterwards.
   - Set filters with an empty database → chips offer choices → scan → only matching rows appear, counts show matches/total, and export contains only matches.
   - History shows only this session and the "coming soon" note. Relaunch → History is empty, and a new automatic backup exists in the backups folder.
   - Clear cache → the toast explains what happened; the next rescan downloads fresh.
   - No purple/blue anywhere. The logo shows in the sidebar. The taskbar/Dock shows the KiA icon.
4. Summarize for the owner: what was done per item, what's still open (e.g. 1G, restore), and screenshots of the header, a no-listings company, the filters banner and Settings → Data & storage.

---

## Appendix A — Bundled assets and where they go

| File in `assets/` | Purpose | Goes to |
|---|---|---|
| `logo/logo-kia.svg` | In-app logo. Vector traced from the owner's KiA image; `fill="currentColor"` | `ui/img/logo-kia.svg`, inlined in the sidebar |
| `logo/logo-kia-light-on-dark.png`, `logo/logo-kia-dark-on-light.png` | README / store images | `docs/` or README |
| `icon/knowitall.ico` | Windows exe, window and taskbar icon (16, 24, 32, 48, 64, 128, 256 px, pixel-crisp at each size) | `knowitall/assets/icons/` |
| `icon/knowitall.icns` | macOS app bundle icon (Apple rounded-square template) | `knowitall/assets/icons/` |
| `icon/icon-macos-1024.png` | macOS Dock icon when run from source | `knowitall/assets/icons/` |
| `icon/icon-{16…1024}.png` | Square icon, black background, glyph `#d8dae0` | `knowitall/assets/icons/` (at least 256 and 1024) |
| `icon/icon.svg`, `icon/icon-glyph.svg` | Vector master (square) and glyph-only (`currentColor`) | keep as source; glyph can serve as a small brand mark |
| `source/KiA-logo-original.png`, `source/icon-original.png` | The owner's original images | reference only |

The icon glyph is pixel art on a 4 × 5 grid. Every PNG size is drawn on whole pixels, so none is blurry. If the owner later wants the icon on a red background, change only the background color in `icon.svg` and regenerate. Don't redraw the glyph.

## Appendix B — Color tokens (`ui/css/tokens.css`)

Surfaces and grey text stay as they are. Only these change:

| Token | Dark theme | Light theme | Notes |
|---|---|---|---|
| `--accent` | `#bf1704` | `#bf1704` | Fills: primary button, chips on, switches on, segmented selected |
| `--accent-hover` | `#d81a05` | `#9e1303` | Dark: white on it = 5.16:1. Light: 8.24:1 |
| `--accent-text` | `#fb6655` | `#b91604` | Text/glyphs. Dark: ≥4.51:1 on every surface. Light: ≥4.63:1 |
| `--on-accent` | `#ffffff` | `#ffffff` | White on `#bf1704` = 6.29:1 (`#eceef3` fails on the dark hover) |
| `--focus` | `#fb6655` | `#bf1704` | Focus ring ≥3:1 on every surface (dark ≥4.51, light ≥4.41) |
| `--glow` | `rgba(191, 23, 4, 0.35)` | `rgba(191, 23, 4, 0.18)` | Selection, drop hints, new-row flash |

All pairs were checked against the same formula `tests/test_ui_contrast.py` uses. After you edit, run that test; it must pass without changing the test.

## Appendix C — User-facing copy

Keep the tone plain. The UI/UX skill may tighten wording, but must keep the meaning.

- `no_listings`: **"No listings available on this page."** Secondary: "KnowItAll found {domain}'s careers page but couldn't read any job postings from it." Action: "Open careers page"
- `no_careers_page`: "No careers page found on {domain}." Secondary: "Try the company's main website or its careers site address."
- `unreachable`: "Couldn't reach {domain}." Secondary: "Check the address or your internet connection, then scan again."
- `blocked`: "{domain} blocked automated access." Secondary: "The site's bot protection stopped the scan." Action: "Open careers page"
- `unsupported`: "{domain} uses {platform}, which KnowItAll can't read yet." Action: "Open careers page"
- `timed_out`: "Stopped after {limit} — this site is slow. {n} postings found."
- `stopped`: "Stopped. {n} postings found."
- `error`: "Something went wrong scanning {domain}." Secondary: "Details are in the log."
- Parent domain note: "Careers for {domain} are hosted on {host}."
- LinkedIn/Indeed only: "{domain} lists its jobs on {LinkedIn/Indeed}, which KnowItAll doesn't scan."
- History footer: "New history features coming soon." / "Earlier sessions are saved in backups (Settings → Data & storage)."
- Clear cache toast: "Cleared {n} saved pages ({size}). The next scan of each company downloads everything fresh."
- Duplicate submit toast: "Already scanning {domain}."
- Waiting row: "Waiting — {k} ahead"
