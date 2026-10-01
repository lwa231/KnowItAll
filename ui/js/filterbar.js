// The global filter bar in the header: search box, Listed/Gone/Closed switch, and the facet chips.
// Counts on the chips come from the server (/api/jobs facets), computed for the current search and the
// other filters, so a chip never offers a choice that would leave nothing.
import * as api from './api.js';
import { state, on, emit } from './state.js';
import { filters, STATUSES, toParams, activeCount, setFilters, toggleValue, clearKey, clearFilters, saveFiltersSoon } from './filters.js';
import { counts, refreshCounts } from './counts.js';
import { openPopover, closePopover, isOpen } from './popover.js';
import { announce } from './notify.js';
import { $, $$, esc, debounce, throttle, fmtNum, countryName, svgIcon } from './util.js';

const WORKPLACE_LABELS = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site', unknown: 'Not stated' };
const TYPE_LABELS = { full_time: 'Full-time', part_time: 'Part-time', contract: 'Contract', intern: 'Intern', other: 'Other', unknown: 'Not stated' };
const POSTED = [[null, 'Any time'], [1, 'Last 24 hours'], [7, 'Last 7 days'], [30, 'Last 30 days'], [90, 'Last 90 days']];

let facets = null;            // last answer from the server: {workplace: [{value, count}], ...}
let total = null;
let announceNext = false;
let popoverHandle = null;

/* ------------------------------------------------------------------ data */
export function scopeDomain() { return state.activeTab === 'ALL' ? null : state.activeTab; }

export async function refreshFacets() {
    try {
        const params = toParams({ limit: 1, facets: 1, domain: scopeDomain(), run_id: scopeDomain() ? state.runView[scopeDomain()] : null });
        const data = await api.get(`/api/jobs?${params}`);
        facets = data.facets;
        total = data.total;
        computeHints();
    } catch { /* keep the last answer; the panes show their own errors */ }
    paintSummary();
    paintBanner();
    popoverHandle?.refresh();
    if (announceNext) {
        announceNext = false;
        if (total !== null) announce(`${fmtNum(total)} ${total === 1 ? 'posting' : 'postings'}`);
    }
}
/** Postings hidden only because they do not state the field being filtered (the feed offers to show them). */
const HINT_FIELDS = { workplace: 'workplace', employment_type: 'employment type', country: 'location' };
function computeHints() {
    const hidden = {};
    for (const key of Object.keys(HINT_FIELDS)) {
        const narrowing = key === 'country' ? (filters.country.some(v => v !== 'unknown') || filters.region_group.length) : filters[key].some(v => v !== 'unknown');
        const n = unknownCount(key);
        if (narrowing && !filters[key].includes('unknown') && n) hidden[key] = n;
    }
    counts.hidden = hidden;
    emit('hints');
}
const refreshFacetsSoon = debounce(refreshFacets, 200);
const refreshCountsSoon = debounce(refreshCounts, 200);
export const refreshFacetsLive = throttle(refreshFacets, 2500);        // while a scan is streaming rows in

const WORKPLACE_VOCAB = ['remote', 'hybrid', 'onsite'];
const TYPE_VOCAB = ['full_time', 'part_time', 'contract', 'intern', 'other'];
const SOURCE_LABELS = { greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby', smartrecruiters: 'SmartRecruiters', workday: 'Workday',
    oracle: 'Oracle Recruiting', 'json-ld': 'Page data (JSON-LD)', 'json-sniffed': 'Data the page loaded', 'html-heuristic': 'Links on the page' };
const SOURCE_VOCAB = Object.keys(SOURCE_LABELS);

let allCountries = null;                   // every country, from /api/geo/countries: the choices exist before anything is scanned
async function loadCountries() {
    if (allCountries) return;
    try { allCountries = (await api.get('/api/geo/countries')).countries; popoverHandle?.refresh(); } catch { /* the countries seen so far still show */ }
}

const countOf = (key, value) => (facets?.[key] || []).find(f => f.value === value)?.count;
const unknownCount = key => countOf(key, null);

/** Every choice a filter can make, not only the values already in the database; counts are filled in where known. */
function options(key, vocab = [], labels = {}) {
    const values = [...vocab];
    (facets?.[key] || []).forEach(f => { if (f.value !== null && !values.includes(f.value)) values.push(f.value); });
    filters[key].forEach(v => { if (v !== 'unknown' && !values.includes(v)) values.push(v); });      // a selected choice never disappears
    return values.map(value => ({
        value, count: facets ? (countOf(key, value) || 0) : undefined, label: (facets?.[key] || []).find(f => f.value === value)?.label,
        text: labels[value] || (facets?.[key] || []).find(f => f.value === value)?.label || value,
    }));
}

/* ---------------------------------------------------------------- painting */
function chipSpecs() {
    return [
        { id: 'workplace', label: 'Workplace', value: () => shown('workplace', WORKPLACE_LABELS), open: openMulti('workplace', 'Workplace', WORKPLACE_VOCAB, WORKPLACE_LABELS, { unknown: true }) },
        { id: 'employment_type', label: 'Type', value: () => shown('employment_type', TYPE_LABELS), open: openMulti('employment_type', 'Employment type', TYPE_VOCAB, TYPE_LABELS, { unknown: true }) },
        { id: 'region', label: 'Region', value: regionText, open: openRegion },
        { id: 'department', label: 'Department', value: () => shown('department', {}), open: openMulti('department', 'Department', [], {}, { unknown: true, searchable: true, custom: true }) },
        { id: 'source', label: 'Source', value: () => shown('source', SOURCE_LABELS), open: openMulti('source', 'Source', SOURCE_VOCAB, SOURCE_LABELS) },
        { id: 'posted', label: 'Posted', value: () => filters.posted_within_days ? (POSTED.find(p => p[0] === filters.posted_within_days)?.[1] || `${filters.posted_within_days} days`) : '', open: openPosted },
    ];
}

/** What a chip shows: the chosen values, with "+ not stated" when that box is ticked. */
function shown(key, labels) {
    const chosen = filters[key].filter(v => v !== 'unknown').map(v => labels[v] || v);
    return chosen.length && filters[key].includes('unknown') ? [...chosen, 'not stated'].join(', ') : chosen.join(', ');
}

function regionText() {
    const names = [...filters.region_group, ...filters.country.filter(c => c !== 'unknown').map(countryName)];
    return names.length && filters.country.includes('unknown') ? [...names, 'no location'].join(', ') : names.join(', ');
}

function chipCount(id) {
    if (id === 'region') return filters.region_group.length + filters.country.filter(c => c !== 'unknown').length;
    if (id === 'posted') return filters.posted_within_days ? 1 : 0;
    return filters[id].filter(v => v !== 'unknown').length;
}

/** Create the chips once, then only update their text and state: an open popover keeps a live anchor and focus. */
function buildChips() {
    const box = $('#filterChips');
    box.innerHTML = chipSpecs().map(spec => `<button type="button" class="chip" data-chip="${spec.id}" aria-haspopup="dialog" aria-expanded="false">
            <span>${esc(spec.label)}</span><span class="chip-value truncate" hidden></span>${svgIcon('chevron')}</button>`).join('')
        + `<button type="button" class="chip" data-chip="new" aria-pressed="false">New only</button>`;
}

export function paintChips() {
    const box = $('#filterChips');
    if (!box.children.length) buildChips();
    chipSpecs().forEach(spec => {
        const button = box.querySelector(`[data-chip="${spec.id}"]`);
        const n = chipCount(spec.id);
        const value = button.querySelector('.chip-value');
        button.classList.toggle('has-value', n > 0);
        value.hidden = n === 0;
        value.textContent = n ? `· ${spec.value()}` : '';
    });
    box.querySelector('[data-chip="new"]').setAttribute('aria-pressed', String(filters.new_only));
}

function paintSummary() {
    const node = $('#resultSummary');
    if (total === null) { node.textContent = ''; return; }
    const what = { current: 'postings', missing: 'gone', closed: 'closed' }[filters.status];
    node.textContent = `${fmtNum(total)} ${filters.status === 'current' && total === 1 ? 'posting' : what}${activeCount() ? ' match' : ''}`;
}

function paintStatic() {
    $('#searchClear').hidden = !filters.q;
    const seg = $$('#statusSeg button');
    seg.forEach(button => {
        const checked = button.dataset.status === filters.status;
        button.setAttribute('aria-checked', String(checked));
        button.tabIndex = checked ? 0 : -1;
    });
    if (document.activeElement !== $('#searchInput')) $('#searchInput').value = filters.q;
}

/** "Filters on: Remote · United States · last 7 days — showing 23 of 1,204." with Edit and Clear. */
export function paintBanner() {
    const banner = $('#filterBanner');
    const active = activeCount() > 0;
    banner.hidden = !active;
    if (!active) return;
    const parts = chipSpecs().filter(spec => chipCount(spec.id)).map(spec => spec.value());
    if (filters.q) parts.push(`“${filters.q}”`);
    if (filters.new_only) parts.push('new only');
    const domain = scopeDomain();
    const matching = domain ? counts.matchByDomain[domain] || 0 : counts.matching;
    const all = domain ? counts.totalByDomain[domain] || 0 : counts.total;
    const showing = counts.ready && counts.filtering ? ` — showing ${fmtNum(matching)} of ${fmtNum(all)}.` : '';
    $('#filterBannerText').textContent = `Filters on: ${parts.join(' · ')}${showing}`;
}

export function paintAll() { paintChips(); paintStatic(); paintSummary(); paintBanner(); }

/* ---------------------------------------------------------------- popovers */
function checkbox(id, text, count, checked) {
    return `<button type="button" class="option" role="checkbox" id="${id}" aria-checked="${checked}">
        <span class="box">${svgIcon('check')}</span><span class="truncate">${esc(text)}</span>${count === undefined ? '' : `<span class="count">${fmtNum(count)}</span>`}</button>`;
}

function arrowKeys(container) {
    container.addEventListener('keydown', event => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const items = $$('.option', container);
        const at = items.indexOf(document.activeElement);
        if (at < 0) return;
        event.preventDefault();
        items[(at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    });
}

function footer(clearAction) {
    return `<div class="popover-foot"><button type="button" class="btn btn-quiet" data-pop-clear>Clear</button><button type="button" class="btn" data-pop-done>Done</button></div>`;
}

function wireFooter(panel, close, clearAction) {
    panel.querySelector('[data-pop-clear]').onclick = () => clearAction();
    panel.querySelector('[data-pop-done]').onclick = close;
}

/** "Include postings that don't say" for one filter: off unless ticked, and it only widens a choice that was made. */
function unknownRow(key, noun) {
    const n = unknownCount(key);
    return `<div class="popover-rule"></div>
        ${checkbox(`opt-${key}-unknown`, `Include postings that don't say${noun ? ` ${noun}` : ''}`, n === undefined ? undefined : n, filters[key].includes('unknown'))}`;
}

const openMulti = (key, title, vocab, labels, { unknown = false, searchable = false, custom = false } = {}) => anchor => {
    let needle = '';
    popoverHandle = openPopover({
        anchor, label: `${title} filter`, width: 300, onClose: () => { popoverHandle = null; },
        render(panel, close) {
            const all = options(key, vocab, labels).filter(o => !needle || o.text.toLowerCase().includes(needle.toLowerCase()));
            const exact = all.some(o => o.text.toLowerCase() === needle.trim().toLowerCase());
            const add = custom && needle.trim() && !exact ? `<button type="button" class="option" id="opt-${key}-add"><span class="box">${svgIcon('check')}</span><span class="truncate">Add “${esc(needle.trim())}”</span></button>` : '';
            const rows = all.map((o, i) => `<button type="button" class="option" role="checkbox" id="opt-${key}-${i}" aria-checked="${filters[key].includes(o.value)}">
                <span class="box">${svgIcon('check')}</span><span class="truncate">${esc(o.text)}</span>${o.count === undefined ? '' : `<span class="count">${o.count ? fmtNum(o.count) : '0 so far'}</span>`}</button>`).join('');
            panel.innerHTML = `<div class="popover-title label">${esc(title)}</div>
                ${searchable ? `<input class="input" type="search" id="pop-search" placeholder="${custom ? 'Find or type a name…' : 'Find…'}" aria-label="Find in ${esc(title)}" value="${esc(needle)}">` : ''}
                <div class="popover-body" role="group" aria-label="${esc(title)}">${add}${rows || (add ? '' : `<div class="option-empty">${facets ? 'Nothing matches.' : 'Loading…'}</div>`)}</div>
                ${unknown ? unknownRow(key, HINT_FIELDS[key] === 'location' ? 'a location' : key === 'department' ? 'a department' : HINT_FIELDS[key]) : ''}${footer()}`;
            $$('.option[id^="opt-' + key + '-"]', panel).forEach(button => {
                if (button.id.endsWith('-add')) button.onclick = () => { toggleValue(key, needle.trim()); needle = ''; };
                else if (button.id.endsWith('-unknown')) button.onclick = () => toggleValue(key, 'unknown');
                else button.onclick = () => toggleValue(key, all[Number(button.id.split('-').pop())].value);
            });
            arrowKeys(panel);
            wireFooter(panel, close, () => clearKey(key));
            const search = panel.querySelector('#pop-search');
            if (search) {
                search.oninput = () => { needle = search.value; popoverHandle.refresh(); };
                search.onkeydown = event => { if (event.key === 'Enter' && custom && needle.trim()) { event.preventDefault(); toggleValue(key, needle.trim()); needle = ''; } };
            }
            return search || panel.querySelector('.option');
        },
    });
};

function openRegion(anchor) {
    let needle = '';
    loadCountries();
    popoverHandle = openPopover({
        anchor, label: 'Region filter', width: 340, onClose: () => { popoverHandle = null; },
        render(panel, close) {
            const groups = (facets?.region_group || []).map(g => ({ value: g.value, text: `${g.label} (${g.value})` }));
            const inDb = new Map((facets?.country || []).filter(f => f.value).map(f => [f.value, f.count]));
            const countries = (allCountries || []).map(c => ({ value: c.code, text: c.name, count: facets ? (inDb.get(c.code) || 0) : undefined }))
                .filter(c => !needle || c.text.toLowerCase().includes(needle.toLowerCase()) || c.value.toLowerCase() === needle.toLowerCase());
            // chosen ones first, then those that have postings, then the rest alphabetically
            countries.sort((a, b) => (filters.country.includes(b.value) - filters.country.includes(a.value)) || ((b.count || 0) - (a.count || 0)));
            const row = (id, text, count, on) => checkbox(id, text, count === undefined ? undefined : count || undefined, on).replace('</button>', `${count === 0 ? '<span class="count">0 so far</span>' : ''}</button>`);
            panel.innerHTML = `<div class="popover-title label">Region</div>
                <input class="input" type="search" id="pop-search" placeholder="Find a country…" aria-label="Find a country" value="${esc(needle)}">
                <div class="popover-body">
                    ${needle ? '' : `<div class="popover-title label">Regions</div>${groups.map((g, i) => checkbox(`opt-group-${i}`, g.text, undefined, filters.region_group.includes(g.value))).join('')}`}
                    <div class="popover-title label">Countries</div>
                    ${allCountries ? (countries.length ? countries.map((c, i) => row(`opt-country-${i}`, c.text, c.count, filters.country.includes(c.value))).join('') : '<div class="option-empty">No country matches.</div>') : '<div class="option-empty">Loading…</div>'}
                </div>${unknownRow('country', 'a location')}${footer()}`;
            $$('[id^="opt-group-"]', panel).forEach((button, i) => button.onclick = () => toggleValue('region_group', groups[i].value));
            $$('[id^="opt-country-"]', panel).forEach((button, i) => button.onclick = () => toggleValue('country', countries[i].value));
            panel.querySelector('#opt-country-unknown').onclick = () => toggleValue('country', 'unknown');
            arrowKeys(panel);
            wireFooter(panel, close, () => { clearKey('region_group'); clearKey('country'); });
            const search = panel.querySelector('#pop-search');
            search.oninput = () => { needle = search.value; popoverHandle.refresh(); };
            return search;
        },
    });
}

function openPosted(anchor) {
    popoverHandle = openPopover({
        anchor, label: 'Posted filter', width: 220, onClose: () => { popoverHandle = null; },
        render(panel, close) {
            panel.innerHTML = `<div class="popover-title label">Posted</div><div class="popover-body" role="radiogroup" aria-label="Posted within">
                ${POSTED.map(([days, text], i) => `<button type="button" class="option" role="radio" id="opt-posted-${i}" aria-checked="${(filters.posted_within_days || null) === days}"><span class="radio-dot"></span><span>${text}</span></button>`).join('')}
                </div>`;
            $$('.option', panel).forEach((button, i) => button.onclick = () => { setFilters({ posted_within_days: POSTED[i][0] }); close(); });
            arrowKeys(panel);
            return panel.querySelector('[aria-checked="true"]') || panel.querySelector('.option');
        },
    });
}

function openHelp(anchor) {
    popoverHandle = openPopover({
        anchor, label: 'Search tips', width: 380, onClose: () => { popoverHandle = null; },
        render(panel) {
            panel.innerHTML = `<div class="popover-title label">Search tips</div><div class="popover-body"><div class="help-list">
                <div><code>engineer remote</code> — every word must match</div>
                <div><code>eng</code> — words match as prefixes, so this finds “engineer”</div>
                <div><code>"machine learning"</code> — an exact phrase</div>
                <div><code>engineer -intern</code> or <code>engineer NOT intern</code> — leave something out</div>
                <div><code>backend OR platform</code> — either word</div>
                <div><code>nyc</code>, <code>germany</code>, <code>bay area</code>, <code>EMEA</code> — places also match their cities, countries and regions</div>
                <div>Press <code>/</code> anywhere to jump to this box, <code>Esc</code> to clear it.</div>
                </div></div>`;
            return panel.querySelector('.popover-body');
        },
    });
}

/* ------------------------------------------------------------------ wiring */
export function initFilterBar() {
    const search = $('#searchInput');
    const applySearch = debounce(() => setFilters({ q: search.value.trim() }), 250);
    search.addEventListener('input', () => { $('#searchClear').hidden = !search.value; applySearch(); });
    search.addEventListener('keydown', event => {
        if (event.key === 'Escape' && search.value) { search.value = ''; applySearch.cancel(); setFilters({ q: '' }); event.stopPropagation(); }
        if (event.key === 'Enter') { applySearch.flush(); }
    });
    $('#searchClear').addEventListener('click', () => { search.value = ''; setFilters({ q: '' }); search.focus(); });
    $('#searchHelp').addEventListener('click', event => { isOpen(event.currentTarget) ? closePopover() : openHelp(event.currentTarget); });
    $('#bannerClear').addEventListener('click', () => { clearFilters(); $('#searchInput').value = ''; });
    $('#bannerEdit').addEventListener('click', () => {
        const active = chipSpecs().find(spec => chipCount(spec.id));
        ($(`[data-chip="${active ? active.id : 'workplace'}"]`) || $('[data-chip="workplace"]')).click();
    });

    $('#filterChips').addEventListener('click', event => {
        const chip = event.target.closest('[data-chip]');
        if (!chip) return;
        if (chip.dataset.chip === 'new') { setFilters({ new_only: !filters.new_only }); return; }
        if (isOpen(chip)) { closePopover(); return; }
        chipSpecs().find(s => s.id === chip.dataset.chip).open(chip);
    });

    const seg = $('#statusSeg');
    seg.addEventListener('click', event => {
        const button = event.target.closest('[data-status]');
        if (button) setFilters({ status: button.dataset.status });
    });
    seg.addEventListener('keydown', event => {
        const at = STATUSES.indexOf(filters.status);
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = STATUSES[(at + step + STATUSES.length) % STATUSES.length];
        setFilters({ status: next });
        seg.querySelector(`[data-status="${next}"]`).focus();
    });

    on('filters', () => { announceNext = true; paintAll(); refreshFacetsSoon(); refreshCountsSoon(); saveFiltersSoon(); });
    on('counts', paintBanner);
    on('active-tab', () => { refreshFacetsSoon(); paintBanner(); });
    paintAll();
    refreshFacets();
    refreshCounts();
}
