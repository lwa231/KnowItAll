// Tests for the UI's pure logic. No framework: each test is a function that throws on failure.
import { esc, relTime, fmtBytes, fmtNum, clamp, debounce, throttle, countryName, safeHref, fmtClock } from '/ui/js/util.js';
import { filters, toParams, activeCount, isFiltering, setFilters, toggleValue, clearKey, clearFilters, resetForTests, profileOf, applySaved, unfiltered } from '/ui/js/filters.js';
import { counts, matchesOf } from '/ui/js/counts.js';
import { hintsHTML } from '/ui/js/table.js';
import { PaneData, PAGE, resetPaneDataForTests, paneData } from '/ui/js/jobs.js';
import { rowHTML, bodyHTML, footerHTML, skeletonHTML } from '/ui/js/table.js';
import { geometry } from '/ui/js/workspace.js';
import { state, on, emit } from '/ui/js/state.js';
import { renderQueue } from '/ui/js/queue.js';
import { noListingsHTML, withoutDomain, paintPhases, tickClocks } from '/ui/js/outcomes.js';
import { paintRunState, paintMetrics, initRun } from '/ui/js/run.js';
import { showView, initRail } from '/ui/js/nav.js';
import { initQueuePanel, toggleQueuePanel } from '/ui/js/queuepanel.js';
import { trail, initBreadcrumbs, renderCrumbs } from '/ui/js/breadcrumbs.js';
import { nearestIndex, bindSteppedSlider } from '/ui/js/slider.js';
import { TIME_STOPS, JOB_STOPS, DEPTH_STOPS } from '/ui/js/views/settings.js';
import { DEFAULTS, syncBindings } from '/ui/js/settings.js';

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const eq = (actual, expected, note = '') => {
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) throw new Error(`${note} expected ${e}, got ${a}`);
};
const ok = (value, note = 'expected truthy') => { if (!value) throw new Error(note); };

/* ------------------------------------------------------------- util */
test('esc escapes markup and quotes', () => {
    eq(esc('<img src=x onerror="alert(1)">&\''), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&#39;');
    eq(esc(null), ''); eq(esc(undefined), ''); eq(esc(0), '0');
});
test('relTime buckets', () => {
    const now = Date.parse('2026-01-10T12:00:00Z');
    const ago = ms => new Date(now - ms).toISOString();
    eq(relTime(null), '—'); eq(relTime('garbage'), '—');
    eq(relTime(ago(20_000), now), 'just now'); eq(relTime(ago(5 * 60_000), now), '5m ago'); eq(relTime(ago(3 * 3600_000), now), '3h ago');
    eq(relTime(ago(2 * 86400_000), now), '2d ago'); eq(relTime(ago(14 * 86400_000), now), '2w ago');
    eq(relTime(ago(90 * 86400_000), now), '3mo ago'); eq(relTime(ago(800 * 86400_000), now), '2y ago');
    eq(relTime(new Date(now + 60_000).toISOString(), now), 'just now');
});
test('fmtBytes and fmtNum', () => {
    eq(fmtBytes(0), '0 B'); eq(fmtBytes(1023), '1023 B'); eq(fmtBytes(1536), '1.5 KB'); eq(fmtBytes(5 * 1024 * 1024), '5.0 MB'); eq(fmtBytes(300 * 1024 * 1024), '300 MB');
    eq(fmtNum(1234567), '1,234,567'); eq(fmtNum(null), '0');
});
test('clamp', () => { eq(clamp(5, 1, 3), 3); eq(clamp(-1, 1, 3), 1); eq(clamp(2, 1, 3), 2); });
test('debounce runs once, last args; cancel and flush work', async () => {
    const calls = []; const d = debounce(v => calls.push(v), 40);
    d(1); d(2); d(3); await new Promise(r => setTimeout(r, 90)); eq(calls, [3]);
    d(4); d.cancel(); await new Promise(r => setTimeout(r, 90)); eq(calls, [3]);
    d.flush(5); eq(calls, [3, 5]);
});
test('throttle delivers the first call now and the last call later', async () => {
    const calls = []; const t = throttle(v => calls.push(v), 60);
    t(1); t(2); t(3); eq(calls, [1]); await new Promise(r => setTimeout(r, 120)); eq(calls, [1, 3]);
});
test('countryName', () => { eq(countryName('DE'), 'Germany'); eq(countryName(null), 'Unknown'); eq(countryName('ZZ').length > 0, true); });

/* ------------------------------------------------------------- filters */
test('toParams builds repeated params, skips empty ones and the default status', () => {
    resetForTests();
    eq(toParams().toString(), '');
    setFilters({ q: 'eng -intern', posted_within_days: 7, new_only: true });
    toggleValue('workplace', 'remote'); toggleValue('workplace', 'hybrid'); toggleValue('country', 'DE');
    const p = toParams({ domain: 'a.com', limit: 100, offset: 0, facets: 0, run_id: null, sort: '' });
    eq(p.getAll('workplace'), ['remote', 'hybrid']); eq(p.get('q'), 'eng -intern'); eq(p.get('posted_within_days'), '7');
    eq(p.get('new_only'), '1'); eq(p.get('domain'), 'a.com'); eq(p.get('offset'), '0'); eq(p.has('run_id'), false); eq(p.has('sort'), false);
    ok(!p.has('status'));
    setFilters({ status: 'missing' }); eq(toParams().get('status'), 'missing');
    resetForTests();
});
test('activeCount counts filters, not the status switch', () => {
    resetForTests(); eq(activeCount(), 0); eq(isFiltering(), false);
    setFilters({ status: 'closed' }); eq(activeCount(), 0);
    toggleValue('workplace', 'remote'); toggleValue('workplace', 'hybrid'); eq(activeCount(), 1, 'two values of one filter is one filter');
    setFilters({ q: 'x' }); toggleValue('country', 'US'); setFilters({ new_only: true, posted_within_days: 30 }); eq(activeCount(), 5);
    resetForTests();
});
test('toggleValue, clearKey and clearFilters (which keeps the status)', () => {
    resetForTests();
    toggleValue('source', 'lever'); toggleValue('source', 'lever'); eq(filters.source, []);
    toggleValue('department', 'Sales'); clearKey('department'); eq(filters.department, []);
    setFilters({ q: 'a', new_only: true, status: 'missing', posted_within_days: 7 }); toggleValue('region_group', 'EMEA');
    clearFilters();
    eq([filters.q, filters.new_only, filters.posted_within_days, filters.region_group, filters.status], ['', false, null, [], 'missing']);
    resetForTests();
});
test('filter changes are announced on the bus', () => {
    resetForTests(); let heard = 0; const off = on('filters', () => heard++);
    toggleValue('workplace', 'remote'); setFilters({ q: 'x' }); clearFilters(); off(); eq(heard, 3); resetForTests();
});

/* ------------------------------------------------------------- table */
const job = over => ({ id: 1, title: 'Engineer', url: 'https://x.com/1', company: 'Acme', domain: 'acme.com', location: 'Berlin', workplace: null,
                       employment_type: null, department: null, posted: null, is_new: false, status: 'open', ...over });
test('rowHTML never lets markup through', () => {
    const html = rowHTML(job({ title: '<script>alert(1)</script>', url: 'https://x.com/?a="b"', location: '<b>x</b>', department: '"><img>', company: '<i>' }), 'ALL');
    ok(!/<script|<b>|<img|<i>/.test(html), html);
    ok(html.includes('&lt;script&gt;') && html.includes('&quot;'));
    ok(html.includes('rel="noopener noreferrer"'));
});
test('rowHTML badges, unknowns and the Company column only in ALL', () => {
    const all = rowHTML(job({ workplace: 'remote', employment_type: 'full_time', is_new: true }), 'ALL');
    ok(all.includes('Remote') && all.includes('Full-time') && all.includes('badge-new') && all.includes('Acme'));
    const one = rowHTML(job(), 'acme.com');
    ok(!one.includes('col-company') && one.includes('aria-label="not stated"'));
    ok(rowHTML(job({ url: null }), 'ALL').includes('<span class="job-title">'), 'no link when there is no url');
});
test('gone and closed rows show when, not posted', () => {
    const gone = rowHTML(job({ status: 'missing', missing_since: new Date(Date.now() - 3 * 86400_000).toISOString() }), 'ALL');
    ok(gone.includes('3d ago') && gone.includes('class="row gone"'));
});
test('bodyHTML: skeleton, empty, table with footer outside it', () => {
    resetForTests(); resetPaneDataForTests(); state.companies = []; state.running = false; state.runView = {};
    const pane = new PaneData('ALL');
    ok(bodyHTML(pane).includes('aria-busy="true"'), 'skeleton before the first load');
    pane.loadedOnce = true;
    ok(bodyHTML(pane).includes('Nothing scanned yet'));
    state.companies = [{ domain: 'a.com', state: 'done', jobs_count: 0 }]; ok(bodyHTML(pane).includes('No postings found'));
    state.companies = [{ domain: 'a.com', state: 'scanning', jobs_count: 0 }]; state.running = true; ok(bodyHTML(pane).includes('Scanning'));
    state.companies = [{ domain: 'a.com', state: 'done', jobs_count: 4 }]; state.running = false;
    toggleValue('workplace', 'remote'); ok(bodyHTML(pane).includes('No postings match these filters') && bodyHTML(pane).includes('data-clear-filters'));
    resetForTests(); setFilters({ status: 'missing' }); ok(bodyHTML(pane).includes('Nothing has gone missing yet')); setFilters({ status: 'closed' }); ok(bodyHTML(pane).includes('Nothing has closed yet'));
    resetForTests(); state.running = false; pane.error = 'HTTP 500'; ok(bodyHTML(pane).includes('Could not load postings') && bodyHTML(pane).includes('data-retry')); pane.error = null;
    pane.rows = [job(), job({ id: 2, title: 'B' })]; pane.total = 2;
    const html = bodyHTML(pane);
    ok(html.includes('<table') && html.indexOf('</table>') < html.indexOf('data-sentinel'), 'footer follows the table, not inside it');
    ok(html.includes('2 postings'));
    pane.total = 250; ok(footerHTML(pane).includes('data-load-more') && footerHTML(pane).includes('2 of 250'));
    pane.loadingMore = true; ok(footerHTML(pane).includes('Loading more'));
    state.companies = []; resetPaneDataForTests();
});

/* ------------------------------------------------------------- filters as barriers (phase 3) */
test('"include postings that don\'t say" alone narrows nothing; beside a choice it widens it', () => {
    resetForTests(); toggleValue('workplace', 'unknown');
    eq(activeCount(), 0); eq(toParams().getAll('workplace'), []);
    toggleValue('workplace', 'remote'); eq(activeCount(), 1); eq(toParams().getAll('workplace'), ['unknown', 'remote']);
    resetForTests(); toggleValue('country', 'unknown'); eq(activeCount(), 0);
    toggleValue('region_group', 'EMEA'); eq(toParams().getAll('country'), ['unknown']);
    resetForTests();
});
test('only the chip filters are saved; search, New only and the status switch are not', () => {
    resetForTests(); setFilters({ q: 'x', new_only: true, status: 'closed', posted_within_days: 7 }); toggleValue('workplace', 'remote'); toggleValue('country', 'unknown');
    eq(profileOf(), { workplace: ['remote'], country: ['unknown'], posted_within_days: 7 });
    applySaved({ workplace: ['hybrid'], source: ['lever'], posted_within_days: 30 });
    eq([filters.workplace, filters.source, filters.posted_within_days, filters.q, filters.new_only, filters.status], [['hybrid'], ['lever'], 30, '', false, 'current'], 'restored, and nothing else carried over');
    applySaved(undefined); eq(activeCount(), 0); resetForTests();
});
test('unfiltered() keeps the Listed/Gone/Closed choice and nothing else', () => {
    resetForTests(); toggleValue('workplace', 'remote'); setFilters({ q: 'a', status: 'missing' });
    const p = toParams({}, unfiltered()); eq(p.toString(), 'status=missing'); resetForTests();
});
test('queue counts are matches only while filters are on and answered', () => {
    resetForTests(); Object.assign(counts, { ready: true, filtering: true, matchByDomain: { 'a.com': 12 } });
    eq(matchesOf('a.com'), 12); eq(matchesOf('b.com'), 0); counts.filtering = false; eq(matchesOf('a.com'), null); counts.ready = false; eq(matchesOf('a.com'), null);
});
test('the feed offers to show postings hidden only because the field is not stated', () => {
    counts.hidden = { workplace: 38, country: 1 };
    const html = hintsHTML();
    ok(html.includes("+38 postings don't state workplace — show them") && html.includes("+1 posting doesn't state location — show it") && html.includes('data-show-unknown="workplace"'), html);
    counts.hidden = {}; eq(hintsHTML(), '');
});
test('while scanning with filters on and nothing matching yet, the pane says how many were checked', () => {
    state.runView = {}; state.running = true; resetForTests(); toggleValue('workplace', 'remote');
    state.companies = [company({ state: 'scanning', jobs_count: 340, phase: 'x', started_at: 1 })];
    const html = bodyHTML(paneFor('acme.com'));
    ok(html.includes('0 matches so far (340 postings checked)') && !html.includes('No postings match'), html);
    resetForTests(); state.running = false; state.companies = [];
});

/* ------------------------------------------------------------- scan outcomes (1H) */
const company = over => ({ domain: 'acme.com', company: 'Acme', state: 'done', jobs_count: 0, new_count: 0, missing_count: 0, closed_count: 0,
                           outcome: null, outcome_detail: null, outcome_hint: null, outcome_short: null, careers_url: null, careers_note: null, phase: null, started_at: null, ...over });
const paneFor = key => { resetPaneDataForTests(); const pane = new PaneData(key); pane.loadedOnce = true; return pane; };
const OUTCOMES = {
    no_listings:     { outcome_detail: 'No listings available on this page.', outcome_hint: "KnowItAll found acme.com's careers page but couldn't read any job postings from it.", careers_url: 'https://acme.com/careers' },
    no_careers_page: { outcome_detail: 'No careers page found on acme.com.', outcome_hint: "Try the company's main website or its careers site address." },
    unreachable:     { outcome_detail: "Couldn't reach acme.com.", outcome_hint: 'Check the address or your internet connection, then scan again.' },
    blocked:         { outcome_detail: 'acme.com blocked automated access.', outcome_hint: "The site's bot protection stopped the scan.", careers_url: 'https://acme.com/careers' },
    unsupported:     { outcome_detail: "acme.com uses Taleo, which KnowItAll can't read yet.", careers_url: 'https://acme.taleo.net/jobs' },
    timed_out:       { outcome_detail: 'Stopped after 3 min — this site is slow. 0 postings found.', state: 'stopped' },
    stopped:         { outcome_detail: 'Stopped. 0 postings found.', state: 'stopped' },
    error:           { outcome_detail: 'Something went wrong scanning acme.com.', outcome_hint: 'Details are in the log.', state: 'failed' },
};
test('every outcome is explained in the pane of the company that has it', () => {
    state.runView = {}; state.running = false; resetForTests();
    for (const [outcome, fields] of Object.entries(OUTCOMES)) {
        state.companies = [company({ outcome, ...fields })];
        const html = bodyHTML(paneFor('acme.com'));
        ok(html.includes(esc(fields.outcome_detail)), `${outcome}: message`);
        if (fields.outcome_hint) ok(html.includes(esc(fields.outcome_hint)), `${outcome}: hint`);
        ok(!html.includes('Scanning…'), `${outcome}: never says Scanning`);
        eq(html.includes('Open careers page'), !!fields.careers_url, `${outcome}: the careers link is offered only when there is one`);
    }
    state.companies = [];
});
test('the careers link opens in the system browser and only ever points at http(s)', () => {
    state.runView = {};
    state.companies = [company({ outcome: 'blocked', ...OUTCOMES.blocked })];
    const html = bodyHTML(paneFor('acme.com'));
    ok(html.includes('href="https://acme.com/careers"') && html.includes('target="_blank"') && html.includes('rel="noopener noreferrer"'));
    for (const bad of ['javascript:fetch(1)', 'data:text/html,<b>', '/careers', 'file:///etc/passwd', ' JaVaScRiPt:alert(1)']) {
        state.companies = [company({ outcome: 'blocked', ...OUTCOMES.blocked, careers_url: bad })];
        ok(!bodyHTML(paneFor('acme.com')).includes('Open careers page'), `refused: ${bad}`);
    }
    state.companies = [];
});
test('company A finished with nothing while company B scans: A shows its outcome, not Scanning', () => {
    state.runView = {}; state.running = true; resetForTests();
    state.companies = [company({ domain: 'a.com', outcome: 'blocked', outcome_detail: 'a.com blocked automated access.', careers_url: 'https://a.com/careers' }),
                       company({ domain: 'b.com', state: 'scanning', phase: 'Reading the Greenhouse job board…', started_at: Date.now() / 1000 - 42 })];
    const a = bodyHTML(paneFor('a.com')), b = bodyHTML(paneFor('b.com'));
    ok(a.includes('a.com blocked automated access.') && !a.includes('Scanning…'), a);
    ok(b.includes('Scanning…') && b.includes('Reading the Greenhouse job board…') && b.includes('data-elapsed'), b);
    state.running = false; state.companies = [];
});
test('a scanning pane shows its phase and elapsed time, and both update in place', () => {
    state.runView = {}; state.running = true;
    state.companies = [company({ state: 'scanning', phase: 'Finding the careers page…', started_at: Date.now() / 1000 - 65 })];
    const host = document.createElement('div'); host.innerHTML = bodyHTML(paneFor('acme.com')); document.body.appendChild(host);
    ok(host.querySelector('[data-phase]').textContent === 'Finding the careers page…');
    ok(/^1:0[4-9]$/.test(host.querySelector('[data-elapsed]').textContent), host.querySelector('[data-elapsed]').textContent);
    const node = host.querySelector('[data-phase]');
    state.companies[0].phase = 'Reading the Workday job board…'; paintPhases(host); tickClocks(host);
    ok(host.querySelector('[data-phase]') === node && node.textContent === 'Reading the Workday job board…', 'same node, new text');
    host.remove(); state.running = false; state.companies = [];
});
test('the All pane lists the companies that finished with nothing, below the table', () => {
    state.runView = {}; state.running = false; resetForTests();
    state.companies = [company({ domain: 'good.com', jobs_count: 2 }),
                       company({ domain: 'tesla.com', outcome: 'blocked', outcome_detail: 'tesla.com blocked automated access.', careers_url: 'https://tesla.com/careers' }),
                       company({ domain: 'acme.com', outcome: 'no_listings', ...OUTCOMES.no_listings })];
    const pane = paneFor('ALL'); pane.rows = [job()]; pane.total = 1;
    const html = bodyHTML(pane);
    ok(html.indexOf('</table>') < html.indexOf('Companies with no listings (2)'), 'the list follows the table');
    ok(html.includes('Blocked automated access.') && html.includes('data-open-company="tesla.com"') && html.includes('Open careers page'));
    ok(!html.includes('data-open-company="good.com"'), 'a company with postings is not listed');
    const onlyEmpty = paneFor('ALL'); state.companies = state.companies.filter(c => c.jobs_count === 0);
    const empty = bodyHTML(onlyEmpty);
    ok(empty.includes('None of the 2 companies returned postings.') && empty.includes('Companies with no listings (2)'), empty);
    state.companies = []; eq(noListingsHTML(), '');
});
test('withoutDomain drops the leading domain so the list does not say it twice', () => {
    eq(withoutDomain('tesla.com blocked automated access.', 'tesla.com'), 'Blocked automated access.');
    eq(withoutDomain("Couldn't reach tesla.com.", 'tesla.com'), "Couldn't reach tesla.com.");
    eq(withoutDomain('No listings available on this page.', 'acme.com'), 'No listings available on this page.');
});
test('looking at an older scan never shows the latest scan\'s outcome', () => {
    state.running = false; state.runView = { 'acme.com': 12 };
    state.companies = [company({ outcome: 'blocked', ...OUTCOMES.blocked })];
    const html = bodyHTML(paneFor('acme.com'));
    ok(html.includes('That scan found no postings') && !html.includes('blocked automated access'), html);
    state.runView = {}; state.companies = [];
});
test('filters hide postings only when there are some; with none found the outcome is shown', () => {
    state.runView = {}; state.running = false; resetForTests(); toggleValue('workplace', 'remote');
    state.companies = [company({ outcome: 'no_careers_page', ...OUTCOMES.no_careers_page })];
    ok(bodyHTML(paneFor('acme.com')).includes('No careers page found on acme.com.'), 'nothing was found, so nothing is being hidden');
    state.companies = [company({ state: 'done', jobs_count: 9 })];
    ok(bodyHTML(paneFor('acme.com')).includes('No postings match these filters'));
    resetForTests(); state.companies = [];
});
test('safeHref accepts absolute http(s) URLs only', () => {
    eq(safeHref('https://x.com/a?b=1'), 'https://x.com/a?b=1'); eq(safeHref('http://x.com'), 'http://x.com/');
    for (const bad of ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', '\tjavascript:alert(1)', 'data:text/html,x', 'file:///x', '//evil.com', '/relative', 'x.com/a', '', null, undefined]) eq(safeHref(bad), '', `refused ${bad}`);
});
test('rowHTML renders a scraped javascript: address as plain text, not a link', () => {
    const html = rowHTML(job({ url: "javascript:fetch('/api/quit')" }), 'ALL');
    ok(!html.includes('href=') && html.includes('<span class="job-title">'), html);
});
test('fmtClock', () => { eq(fmtClock(0), '0:00'); eq(fmtClock(65.9), '1:05'); eq(fmtClock(3600), '60:00'); eq(fmtClock(-5), '0:00'); eq(fmtClock('x'), '0:00'); });

test('a waiting company says how many are ahead, in its pane and its queue row', () => {
    state.runView = {}; state.running = true; state.activeTab = 'ALL';
    state.companies = [company({ domain: 'a.com', state: 'waiting', waiting_ahead: 2 })];
    ok(bodyHTML(paneFor('a.com')).includes('Waiting — 2 ahead'));
    renderQueue();
    ok(document.getElementById('queueList').textContent.includes('Waiting — 2 ahead'));
    state.companies = [company({ domain: 'a.com', state: 'ready' })];
    ok(bodyHTML(paneFor('a.com')).includes('Ready to scan'));
    state.running = false; state.companies = []; renderQueue();
});

/* ------------------------------------------------------------- the sidebar queue is updated in place */
test('queue rows are updated in place: keyboard focus survives server messages', () => {
    state.running = true; state.activeTab = 'ALL';
    state.companies = [company({ domain: 'a.com', state: 'scanning', jobs_count: 3, phase: 'Finding the careers page…' }), company({ domain: 'b.com', state: 'waiting', waiting_ahead: 0 })];
    renderQueue();
    const list = document.getElementById('queueList');
    const rowA = list.children[0], stopA = rowA.querySelector('[data-stop]');
    ok(!stopA.hidden && rowA.textContent.includes('Finding the careers page…'), 'a scanning row shows its phase and a Stop button');
    stopA.focus(); eq(document.activeElement === stopA, true, 'focus set');
    for (const phase of ['Reading the Greenhouse job board…', 'Reading Greenhouse jobs 50/120…']) {
        state.companies[0] = { ...state.companies[0], phase, jobs_count: state.companies[0].jobs_count + 10 }; renderQueue();
    }
    ok(list.children[0] === rowA && document.activeElement === stopA, 'same row, focus kept on the Stop button');
    ok(rowA.textContent.includes('Reading Greenhouse jobs 50/120…') && rowA.textContent.includes('… 23'));
    state.companies[0] = { ...state.companies[0], state: 'done', outcome: 'blocked', outcome_short: 'blocked', outcome_detail: 'a.com blocked automated access.' }; renderQueue();
    ok(list.children[0] === rowA && stopA.hidden, 'finished: the Stop button is hidden, the row is the same');
    ok(rowA.querySelector('[data-reason]').textContent === 'blocked' && rowA.querySelector('[data-glyph]').textContent === '!', 'glyph and reason say blocked');
    ok(rowA.querySelector('.queue-main').className.includes('has-reason'));
    state.companies = [state.companies[1], state.companies[0]]; renderQueue();
    ok(list.children[1] === rowA, 'reordered without rebuilding');
    state.companies = [state.companies[1]]; renderQueue();
    eq(list.children.length, 1, 'a removed company leaves'); state.companies = []; renderQueue(); ok(list.textContent.includes('Nothing queued yet.'));
    state.running = false;
});
test('queue rows: a plain success stays on one line, every other outcome says why', () => {
    state.running = false; state.activeTab = 'ALL';
    state.companies = [company({ domain: 'ok.com', outcome: 'found', jobs_count: 715 }), ...['no_listings', 'no_careers_page', 'unreachable', 'blocked', 'unsupported', 'timed_out', 'stopped', 'error']
        .map(outcome => company({ domain: `${outcome}.com`, outcome, outcome_short: outcome.replace(/_/g, ' '), outcome_detail: 'x', state: outcome === 'error' ? 'failed' : outcome === 'timed_out' || outcome === 'stopped' ? 'stopped' : 'done' }))];
    renderQueue();
    const items = [...document.getElementById('queueList').children];
    ok(items[0].querySelector('[data-reason]').hidden && items[0].querySelector('[data-glyph]').textContent === '●', 'found: one line');
    const glyphs = items.slice(1).map(li => li.querySelector('[data-glyph]').textContent);
    eq(glyphs, ['–', '–', '!', '!', '–', '!', '■', '×'], 'one glyph per kind of ending');
    ok(items.slice(1).every(li => !li.querySelector('[data-reason]').hidden), 'each says why');
    ok(items.slice(1).every(li => li.querySelector('[data-sr]').textContent.length > 10), 'and has text for screen readers');
    state.companies = []; renderQueue();
});

test('sortable headers carry aria-sort and a button', () => {
    resetPaneDataForTests(); const pane = new PaneData('ALL'); pane.loadedOnce = true; pane.rows = [job()]; pane.total = 1; pane.sortKey = 'title'; pane.sortDir = 'desc';
    const html = bodyHTML(pane);
    ok(html.includes('aria-sort="descending"') && html.includes('data-sort="title"') && html.includes('aria-sort="none"'));
    ok(skeletonHTML('ALL').includes('skeleton'));
});

/* ------------------------------------------------------------- PaneData with a fake server */
function fakeServer(total) {
    const requests = [];
    const jobs = Array.from({ length: total }, (_, i) => job({ id: i + 1, title: `Job ${i + 1}` }));
    window.fetch = async url => {
        const q = new URL(url, location.origin).searchParams; requests.push(Object.fromEntries(q));
        const offset = Number(q.get('offset') || 0), limit = Number(q.get('limit') || 100);
        return new Response(JSON.stringify({ jobs: jobs.slice(offset, offset + limit), total, limit, offset }), { status: 200 });
    };
    return requests;
}
const realFetch = window.fetch;
const settle = () => new Promise(r => setTimeout(r, 30));

test('PaneData pages through results and stops at the total', async () => {
    resetForTests(); resetPaneDataForTests(); const requests = fakeServer(250);
    const pane = new PaneData('ALL');
    await pane.refresh(); eq([pane.rows.length, pane.total, pane.hasMore, pane.loadedOnce], [100, 250, true, true]);
    await pane.loadMore(); eq(pane.rows.length, 200); await pane.loadMore(); eq([pane.rows.length, pane.hasMore], [250, false]);
    await pane.loadMore(); eq(requests.length, 3, 'no request once everything is loaded');
    eq(requests.map(r => r.offset), ['0', '100', '200']); eq(new Set(pane.rows.map(r => r.id)).size, 250);
    ok(!('domain' in requests[0]), 'ALL sends no domain');
});
test('PaneData asks for its company, run, sort and filters', async () => {
    resetForTests(); resetPaneDataForTests(); const requests = fakeServer(5);
    state.runView = { 'a.com': '7' }; toggleValue('workplace', 'remote'); setFilters({ q: 'eng' });
    const pane = new PaneData('a.com'); pane.sortKey = 'title'; pane.sortDir = 'asc';
    await pane.refresh();
    eq([requests[0].domain, requests[0].run_id, requests[0].sort, requests[0].order, requests[0].workplace, requests[0].q, requests[0].facets], ['a.com', '7', 'title', 'asc', 'remote', 'eng', '0']);
    state.runView = {}; resetForTests();
});
test('PaneData ignores an answer to a question that has been replaced', async () => {
    resetForTests(); resetPaneDataForTests();
    const answers = [];
    window.fetch = url => new Promise(resolve => answers.push({ url, resolve }));
    const pane = new PaneData('ALL');
    const first = pane.refresh(); const second = pane.refresh();
    const body = n => new Response(JSON.stringify({ jobs: [job({ title: `answer ${n}` })], total: n, limit: 100, offset: 0 }), { status: 200 });
    answers[1].resolve(body(2)); await second;                          // the newer question is answered first...
    answers[0].resolve(body(1)); await first;                           // ...and the stale answer arrives late
    eq([pane.rows[0].title, pane.total], ['answer 2', 2]);
});
test('PaneData sort cycles default -> asc -> desc -> default and refetches', async () => {
    resetForTests(); resetPaneDataForTests(); const requests = fakeServer(3); const pane = new PaneData('ALL');
    await pane.cycleSort('title'); eq([pane.sortKey, pane.sortDir, requests.at(-1).order], ['title', 'asc', 'asc']);
    await pane.cycleSort('title'); eq([pane.sortDir, requests.at(-1).order], ['desc', 'desc']);
    await pane.cycleSort('title'); eq([pane.sortKey, 'sort' in requests.at(-1)], [null, false]);
    await pane.cycleSort('company'); await pane.cycleSort('location'); eq([pane.sortKey, pane.sortDir], ['location', 'asc'], 'a new column starts ascending');
});
test('PaneData reports errors and can retry', async () => {
    resetForTests(); resetPaneDataForTests();
    window.fetch = async () => new Response(JSON.stringify({ error: 'boom' }), { status: 500 });
    const pane = new PaneData('ALL'); await pane.refresh(); eq([pane.error, pane.loading, pane.rows.length], ['boom', false, 0]);
    fakeServer(2); await pane.refresh(); eq([pane.error, pane.rows.length], [null, 2]);
});
test('a live refresh keeps loaded rows and the scroll position, a new query resets it', async () => {
    resetForTests(); resetPaneDataForTests(); const requests = fakeServer(400); const pane = new PaneData('ALL');
    await pane.refresh(); await pane.loadMore(); await pane.loadMore(); eq(pane.rows.length, 300);
    pane.resetScroll = false;                                            // (painting the pane consumes this flag)
    await pane.refresh({ live: true }); eq([pane.rows.length, requests.at(-1).limit, pane.resetScroll], [300, '300', false]);
    await pane.refresh(); eq([pane.rows.length, pane.resetScroll], [100, true]);
});
test('paneData() keeps one PaneData per target', () => {
    resetPaneDataForTests(); ok(paneData('ALL') === paneData('ALL')); ok(paneData('ALL') !== paneData('a.com'));
});

/* ------------------------------------------------------------- dock geometry */
test('dock geometry: one tile fills, two split, four make quarters', () => {
    const none = new Set();
    eq(geometry({ tl: 'A', tr: null, bl: null, br: null }, none).tl, { left: '0%', top: '0%', width: '100%', height: '100%' });
    const two = geometry({ tl: 'A', tr: 'B', bl: null, br: null }, none);
    eq([two.tl.width, two.tr.left, two.tr.width, two.tl.height], ['50%', '50%', '50%', '100%']);
    const four = geometry({ tl: 'A', tr: 'B', bl: 'C', br: 'D' }, none);
    eq([four.tl.height, four.bl.top, four.br.left, four.br.top], ['50%', '50%', '50%', '50%']);
    const stacked = geometry({ tl: 'A', tr: null, bl: 'C', br: null }, none);
    eq([stacked.tl.width, stacked.tl.height, stacked.bl.top], ['100%', '50%', '50%']);
});
test('dock geometry: a folded pane gives its space to the one below', () => {
    const g = geometry({ tl: 'A', tr: null, bl: 'C', br: null }, new Set(['A']));
    eq([g.tl.height, g.bl.top], ['36px', '36px']); ok(g.bl.height.startsWith('calc(100% - 36px'));
    eq(geometry({ tl: 'A', tr: null, bl: null, br: null }, new Set(['A'])).tl.height, '36px');
});
test('state bus: handlers can unsubscribe and a failing handler does not stop the rest', () => {
    let a = 0, b = 0; const offA = on('probe', () => a++); on('probe', () => { throw new Error('x'); }); on('probe', () => b++);
    const originalError = console.error; console.error = () => {}; emit('probe'); offA(); emit('probe'); console.error = originalError;
    eq([a, b], [1, 2]);
});


/* ------------------------------------------------------------- Phase 6: rail, queue panel, breadcrumbs, sliders, status */
const $ = selector => document.querySelector(selector);
const tick = ms => new Promise(resolve => setTimeout(resolve, ms));
const wide = () => { const real = window.matchMedia; window.matchMedia = query => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }); return () => { window.matchMedia = real; }; };
const cssVar = name => { const probe = document.createElement('i'); probe.style.color = `var(--${name})`; $('#fixture').append(probe); const value = getComputedStyle(probe).color; probe.remove(); return value; };
const resetNav = () => { state.activeView = 'scraper'; state.activeTab = 'ALL'; state.runView = {}; state.openedFrom = null; state.settingsSection = null; };
const crumbText = () => [...document.querySelectorAll('#crumbList li:not(.crumb-sep)')].map(li => li.textContent.trim() || li.querySelector('[aria-label]')?.getAttribute('aria-label'));
const posts = [];
const mockApi = () => { posts.length = 0; window.fetch = async (url, init = {}) => { posts.push({ url: String(url), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null }); const isSave = String(url).endsWith('/api/settings') && init.method === 'POST';
    return new Response(JSON.stringify(isSave ? { ...state.settings, ...JSON.parse(init.body) } : {}), { status: 200 }); }; };   // saves echo the merged settings, like the server
let navReady = false;
const initNavOnce = () => { if (navReady) return; navReady = true; initRail(); };

test('rail: clicking each item switches the view and moves aria-current; every item has a name', () => {
    mockApi(); initNavOnce();
    for (const name of ['history', 'output', 'settings', 'system', 'scraper']) {
        $(`.rail-item[data-view="${name}"]`).click();
        eq(state.activeView, name);
        eq([...document.querySelectorAll('.rail-item[aria-current="page"]')].map(b => b.dataset.view), [name]);
        ok($(`.view[data-view="${name}"]`).classList.contains('active'));
    }
    const items = [...document.querySelectorAll('.rail-item')];
    eq(items.length, 7); ok(items.every(b => b.getAttribute('aria-label')), 'every rail item has an accessible name');
    ok(items.every(b => b.querySelector('.tip[aria-hidden="true"]')), 'and a tooltip that is hidden from screen readers');
});
test('rail: a tooltip shows on hover and on keyboard focus', () => {
    const rule = [...document.styleSheets].flatMap(sheet => { try { return [...sheet.cssRules]; } catch { return []; } }).map(r => r.cssText).join('\n');
    ok(/\.rail-item:focus-visible \.tip/.test(rule) && /\.rail-item:hover \.tip/.test(rule), 'the CSS shows the tip on hover and :focus-visible');
    const button = $('.rail-item[data-view="history"]'), tip = button.querySelector('.tip');
    eq(getComputedStyle(tip).visibility, 'hidden');
    button.focus();
    if (button.matches(':focus-visible')) eq(getComputedStyle(tip).visibility, 'visible', 'visible while focused by keyboard');
    button.blur();
});
test('rail: Quit posts /api/quit', async () => {
    mockApi(); window.close = () => {}; initRun();              // (stays stubbed: run.js closes the window 300 ms after Quit)
    $('#quitBtn').click(); await tick(20);
    ok(posts.some(p => p.url.endsWith('/api/quit') && p.method === 'POST'), 'posted /api/quit');
});

test('queue panel: the toggle shows/hides it, flips aria-pressed and saves through setSetting', async () => {
    const restore = wide(); mockApi(); resetNav();
    state.settings = { ...DEFAULTS, queue_panel_open: true };
    initQueuePanel(showView);
    eq([$('#queuePanel').hidden, $('#queueToggle').getAttribute('aria-pressed')], [false, 'true']);
    toggleQueuePanel();
    eq([$('#queuePanel').hidden, $('#queueToggle').getAttribute('aria-pressed'), $('#app').dataset.queue], [true, 'false', 'off']);
    await tick(400);
    ok(posts.some(p => p.url.endsWith('/api/settings') && p.body.queue_panel_open === false), 'the closed state is saved');
    toggleQueuePanel();
    eq([$('#queuePanel').hidden, $('#queueToggle').getAttribute('aria-pressed')], [false, 'true']);
    await tick(400); eq(posts.at(-1).body.queue_panel_open, true);
    restore();
});
test('queue panel: it shows on the Scraper view only, and the Queue button brings you back and opens it', async () => {
    const restore = wide(); mockApi(); resetNav();
    state.settings = { ...DEFAULTS, queue_panel_open: true }; initNavOnce();
    $('.rail-item[data-view="settings"]').click();
    eq($('#queuePanel').hidden, true, 'hidden away from the Scraper');
    state.settings = { ...state.settings, queue_panel_open: false };
    $('#queueToggle').click();
    eq([state.activeView, $('#queuePanel').hidden], ['scraper', false], 'switched to the Scraper with the panel open');
    await tick(400); restore(); state.settings = { ...DEFAULTS };
});

test('breadcrumbs: Scraper All tab, company tab, older scan, from History, other views, Settings sections', () => {
    resetNav(); const go = [];
    initBreadcrumbs({ showView: v => go.push(['view', v]), selectTab: t => go.push(['tab', t]), showLatest: d => go.push(['latest', d]) });
    eq(crumbText(), ['Home', 'Scraper']);
    state.activeTab = 'stripe.com'; emit('active-tab', 'stripe.com'); eq(crumbText(), ['Home', 'Scraper', 'stripe.com']);
    state.runView['stripe.com'] = 12; emit('run-view', 'stripe.com'); eq(crumbText(), ['Home', 'Scraper', 'stripe.com', 'Scan #12']);
    state.openedFrom = 'history'; renderCrumbs(); eq(crumbText(), ['Home', 'History', 'stripe.com', 'Scan #12']);
    $('#crumbList [data-act="history"]').click(); eq(go.at(-1), ['view', 'history']);
    $('#crumbList [data-act="latest:stripe.com"]').click(); eq(go.at(-1), ['latest', 'stripe.com']);
    emit('active-tab', 'stripe.com'); eq(state.openedFrom, null, 'switching tabs clears where it was opened from');
    delete state.runView['stripe.com']; state.activeTab = 'ALL';
    for (const view of ['history', 'output', 'system']) { state.activeView = view; renderCrumbs(); eq(crumbText(), ['Home', view[0].toUpperCase() + view.slice(1)]); }
    state.activeView = 'settings'; renderCrumbs(); eq(crumbText(), ['Home', 'Settings']);
    state.settingsSection = 'scanning'; renderCrumbs(); eq(crumbText(), ['Home', 'Settings', 'Scanning']);
    $('#crumbList [data-act="settings"]').click(); eq(go.at(-1), ['view', 'settings']);
    resetNav(); renderCrumbs();
});
test('breadcrumbs: the last crumb is text marked aria-current and Home goes to Scraper / All', () => {
    resetNav(); state.activeTab = 'a.com'; state.activeView = 'scraper'; renderCrumbs();
    const items = document.querySelectorAll('#crumbList li:not(.crumb-sep)'), last = items[items.length - 1];
    const current = last.querySelector('[aria-current="page"]');
    ok(current && current.tagName === 'SPAN' && !last.querySelector('button'), 'the current page is not a link');
    eq(document.querySelectorAll('#crumbList [aria-current="page"]').length, 1);
    ok([...document.querySelectorAll('#crumbList .crumb-sep')].every(li => li.getAttribute('aria-hidden') === 'true'), 'separators are hidden from screen readers');
    eq($('#crumbList [data-act="home"]').getAttribute('aria-label'), 'Home');
    resetNav(); renderCrumbs();
});

test('sliders: index <-> value for time limit, postings and detail depth', () => {
    for (const stops of [TIME_STOPS, JOB_STOPS, DEPTH_STOPS]) stops.forEach((value, i) => eq(nearestIndex(stops, value), i));
    eq(TIME_STOPS, [1, 3, 5]); eq(JOB_STOPS[3], 2000); eq(DEPTH_STOPS[3], 50);
    eq(JOB_STOPS, [100, 500, 1000, 2000, 5000, 10000, 50000, 100000]); eq(DEPTH_STOPS, [0, 10, 25, 50, 100, 250, 500]);
    eq([nearestIndex(JOB_STOPS, 2500), nearestIndex(JOB_STOPS, 99999), nearestIndex(DEPTH_STOPS, 37)], [3, 7, 2]);
});
test('sliders: a saved value between stops shows the nearest stop and is not overwritten', async () => {
    mockApi(); state.settings = { ...DEFAULTS, max_jobs: 2500 };
    const input = $('#setMaxJobs');
    bindSteppedSlider(input, 'max_jobs', JOB_STOPS, n => n.toLocaleString('en-US'), { valueText: n => `${n} postings` });
    eq([input.value, input.closest('.slider-row').querySelector('output').textContent], ['3', '2,500'], 'nearest stop, real value in the readout');
    syncBindings(); await tick(400);
    ok(!posts.some(p => p.url.endsWith('/api/settings')), 'nothing was saved');
    eq(state.settings.max_jobs, 2500);
});
test('sliders: moving one updates the readout and aria-valuetext, and change saves stops[index]', async () => {
    mockApi(); state.settings = { ...DEFAULTS, max_enrich: 50, time_limit_min: 3 };
    const depth = $('#setDepth');
    bindSteppedSlider(depth, 'max_enrich', DEPTH_STOPS, n => `${n} pages`);
    depth.value = '5'; depth.dispatchEvent(new Event('input', { bubbles: true }));
    eq([depth.closest('.slider-row').querySelector('output').textContent, depth.getAttribute('aria-valuetext')], ['250 pages', '250 pages']);
    eq(posts.length, 0, 'input alone saves nothing');
    depth.dispatchEvent(new Event('change', { bubbles: true })); await tick(400);
    eq(posts.at(-1).body, { max_enrich: 250 });
    const limit = $('#setLimit');
    bindSteppedSlider(limit, 'time_limit_min', TIME_STOPS, n => `${n} min`, { valueText: n => `${n} ${n === 1 ? 'minute' : 'minutes'}`, marks: [[0, '1 min'], [1, '3 min'], [2, '5 min']] });
    eq([limit.min, limit.max, limit.value, limit.getAttribute('aria-valuetext')], ['0', '2', '1', '3 minutes']);
    eq([...limit.closest('.setting-slider').querySelectorAll('.marks span')].map(m => [m.textContent, m.style.left]), [['1 min', '0%'], ['3 min', '50%'], ['5 min', '100%']]);
    state.settings = { ...DEFAULTS };
});
test('header: the old Fresh / Max jobs / Detail depth controls are gone', () => {
    for (const id of ['hdrMaxJobs', 'hdrDepth', 'freshSwitch']) eq(document.getElementById(id), null, id);
    ok(!$('.header').querySelector('input[type="number"]'), 'no number inputs in the header');
});

test('status character: logo colour when idle, orange while the queue is active; the label follows', () => {
    state.settings = { ...DEFAULTS }; state.connection = 'live';
    state.running = false; state.companies = []; paintRunState();
    const char = $('#pixelChar'), process = $('#processToggle'), label = $('#processLabel');
    ok(char.classList.contains('char-idle') && !process.classList.contains('running'));
    ok(getComputedStyle(char).boxShadow.includes(cssVar('text-bright')), 'idle shadow is the logo colour');
    eq(getComputedStyle(label).color, cssVar('text-muted'));
    state.running = true; state.companies = [company({ domain: 'a.com', state: 'scanning' })]; paintRunState();
    ok(char.classList.contains('char-active') && process.classList.contains('running'));
    ok(getComputedStyle(char).boxShadow.includes(cssVar('status-active')), 'active shadow is orange');
    eq([label.textContent, getComputedStyle(label).color], ['Scanning', cssVar('status-active')]);
    ok(cssVar('status-active') !== cssVar('accent-text'), 'orange is not the brand red');
    state.running = false; state.companies = []; paintRunState();
    ok(char.classList.contains('char-idle') && !process.classList.contains('running'));
});
test('progress bar: width, percent and aria-valuenow follow the run; the gradient is sized to the track', () => {
    state.companies = [company({ state: 'done', domain: 'a.com' }), company({ state: 'done', domain: 'b.com' }), company({ state: 'scanning', domain: 'c.com' }), company({ state: 'waiting', domain: 'd.com' })];
    paintMetrics();
    const fill = $('#progressFill'), track = $('#progressTrack');
    eq([fill.style.width, $('#progressValue').textContent, track.getAttribute('aria-valuenow')], ['50%', '50%', '50']);
    const width = track.getBoundingClientRect().width;
    ok(width > 0 && getComputedStyle(fill).backgroundSize.startsWith(`${width}px`), `gradient is ${width}px wide (the track), got ${getComputedStyle(fill).backgroundSize}`);
    ok(fill.getBoundingClientRect().width < width, 'while the fill is narrower than the track');
    eq(getComputedStyle($('#progressValue')).color, cssVar('text-bright'));
    state.companies = []; paintMetrics(); eq(fill.style.width, '0%');
});

/* ------------------------------------------------------------- run */
// The real page (GET /) is mounted off screen, so the tests above see the real ids and styles.
{
    const html = await (await fetch('/')).text();
    const page = new DOMParser().parseFromString(html, 'text/html');
    const fixture = document.getElementById('fixture');
    fixture.append(document.importNode(page.querySelector('svg[aria-hidden]'), true), document.importNode(page.querySelector('.app'), true),
        document.importNode(page.getElementById('toasts'), true), document.importNode(page.getElementById('announcer'), true));
}
const lines = []; let failed = 0;
for (const [name, fn] of tests) {
    try { window.fetch = realFetch; await fn(); lines.push(`<span class="pass">PASS</span>  ${esc(name)}`); }
    catch (error) { failed++; lines.push(`<span class="fail">FAIL</span>  ${esc(name)}\n        ${esc(error.message)}`); }
}
window.fetch = realFetch;
document.getElementById('report').innerHTML = lines.join('\n') + `\n\n${tests.length - failed}/${tests.length} passed`;
document.title = failed ? `FAIL ${failed}/${tests.length}` : `PASS ${tests.length}/${tests.length}`;
window.__uiTestResult = { total: tests.length, failed };
