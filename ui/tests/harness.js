// Tests for the UI's pure logic. No framework: each test is a function that throws on failure.
import { esc, relTime, fmtBytes, fmtNum, clamp, debounce, throttle, countryName, safeHref, fmtClock } from '/ui/js/util.js';
import { filters, toParams, activeCount, isFiltering, setFilters, toggleValue, clearKey, clearFilters, resetForTests } from '/ui/js/filters.js';
import { PaneData, PAGE, resetPaneDataForTests, paneData } from '/ui/js/jobs.js';
import { rowHTML, bodyHTML, footerHTML, skeletonHTML } from '/ui/js/table.js';
import { geometry } from '/ui/js/workspace.js';
import { state, on, emit } from '/ui/js/state.js';
import { renderQueue } from '/ui/js/queue.js';
import { noListingsHTML, withoutDomain, paintPhases, tickClocks } from '/ui/js/outcomes.js';

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

/* ------------------------------------------------------------- the sidebar queue is updated in place */
test('queue rows are updated in place: keyboard focus survives server messages', () => {
    state.running = true; state.activeTab = 'ALL';
    state.companies = [company({ domain: 'a.com', state: 'scanning', jobs_count: 3, phase: 'Finding the careers page…' }), company({ domain: 'b.com', state: 'queued' })];
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

/* ------------------------------------------------------------- run */
const lines = []; let failed = 0;
for (const [name, fn] of tests) {
    try { window.fetch = realFetch; await fn(); lines.push(`<span class="pass">PASS</span>  ${esc(name)}`); }
    catch (error) { failed++; lines.push(`<span class="fail">FAIL</span>  ${esc(name)}\n        ${esc(error.message)}`); }
}
window.fetch = realFetch;
document.getElementById('report').innerHTML = lines.join('\n') + `\n\n${tests.length - failed}/${tests.length} passed`;
document.title = failed ? `FAIL ${failed}/${tests.length}` : `PASS ${tests.length}/${tests.length}`;
window.__uiTestResult = { total: tests.length, failed };
