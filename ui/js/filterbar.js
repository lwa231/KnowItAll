// The global filter bar in the header: search box, Listed/Gone/Closed switch, and the facet chips.
// Counts on the chips come from the server (/api/jobs facets), computed for the current search and the
// other filters, so a chip never offers a choice that would leave nothing.
import * as api from './api.js';
import { state, on } from './state.js';
import { filters, LIST_KEYS, STATUSES, toParams, activeCount, setFilters, toggleValue, clearKey, clearFilters } from './filters.js';
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
    } catch { /* keep the last answer; the panes show their own errors */ }
    paintSummary();
    popoverHandle?.refresh();
    if (announceNext) {
        announceNext = false;
        if (total !== null) announce(`${fmtNum(total)} ${total === 1 ? 'posting' : 'postings'}`);
    }
}
const refreshFacetsSoon = debounce(refreshFacets, 200);
export const refreshFacetsLive = throttle(refreshFacets, 2500);        // while a scan is streaming rows in

function options(key, labels = {}) {
    const found = (facets?.[key] || []).map(f => ({ value: f.value === null ? 'unknown' : f.value, count: f.count, label: f.label }));
    filters[key].forEach(value => { if (!found.some(o => o.value === value)) found.push({ value, count: 0 }); });   // a selected choice never disappears
    return found.map(o => ({ ...o, text: labels[o.value] || o.label || o.value }));
}

/* ---------------------------------------------------------------- painting */
function chipSpecs() {
    return [
        { id: 'workplace', label: 'Workplace', value: () => filters.workplace.map(v => WORKPLACE_LABELS[v] || v).join(', '), open: openMulti('workplace', 'Workplace', WORKPLACE_LABELS) },
        { id: 'employment_type', label: 'Type', value: () => filters.employment_type.map(v => TYPE_LABELS[v] || v).join(', '), open: openMulti('employment_type', 'Employment type', TYPE_LABELS) },
        { id: 'region', label: 'Region', value: regionText, open: openRegion },
        { id: 'department', label: 'Department', value: () => filters.department.join(', '), open: openMulti('department', 'Department', {}, true) },
        { id: 'source', label: 'Source', value: () => filters.source.join(', '), open: openMulti('source', 'Source', {}) },
        { id: 'posted', label: 'Posted', value: () => filters.posted_within_days ? (POSTED.find(p => p[0] === filters.posted_within_days)?.[1] || `${filters.posted_within_days} days`) : '', open: openPosted },
    ];
}

function regionText() {
    const names = [...filters.region_group, ...filters.country.map(c => c === 'unknown' ? 'Not stated' : countryName(c))];
    return names.join(', ');
}

function chipCount(id) {
    if (id === 'region') return filters.region_group.length + filters.country.length;
    if (id === 'posted') return filters.posted_within_days ? 1 : 0;
    return filters[id].length;
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
    $('#clearFilters').hidden = activeCount() === 0;
    const seg = $$('#statusSeg button');
    seg.forEach(button => {
        const checked = button.dataset.status === filters.status;
        button.setAttribute('aria-checked', String(checked));
        button.tabIndex = checked ? 0 : -1;
    });
    if (document.activeElement !== $('#searchInput')) $('#searchInput').value = filters.q;
}

export function paintAll() { paintChips(); paintStatic(); paintSummary(); }

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

const openMulti = (key, title, labels, searchable = false) => anchor => {
    let needle = '';
    popoverHandle = openPopover({
        anchor, label: `${title} filter`, width: 280, onClose: () => { popoverHandle = null; },
        render(panel, close) {
            const all = options(key, labels).filter(o => !needle || o.text.toLowerCase().includes(needle.toLowerCase()));
            const body = all.length
                ? all.map((o, i) => checkbox(`opt-${key}-${i}`, o.text, o.count, filters[key].includes(o.value))).join('')
                : `<div class="option-empty">${facets ? 'Nothing to choose from yet.' : 'Loading…'}</div>`;
            panel.innerHTML = `<div class="popover-title label">${esc(title)}</div>
                ${searchable ? `<input class="input" type="search" id="pop-search" placeholder="Find…" aria-label="Find in ${esc(title)}" value="${esc(needle)}">` : ''}
                <div class="popover-body" role="group" aria-label="${esc(title)}">${body}</div>${footer()}`;
            $$('.option', panel).forEach((button, i) => button.onclick = () => toggleValue(key, all[i].value));
            arrowKeys(panel);
            wireFooter(panel, close, () => clearKey(key));
            const search = panel.querySelector('#pop-search');
            if (search) search.oninput = () => { needle = search.value; popoverHandle.refresh(); };
            return search || panel.querySelector('.option');
        },
    });
};

function openRegion(anchor) {
    popoverHandle = openPopover({
        anchor, label: 'Region filter', width: 320, onClose: () => { popoverHandle = null; },
        render(panel, close) {
            const groups = (facets?.region_group || []).map(g => ({ value: g.value, text: `${g.label} (${g.value})` }));
            const countries = options('country').map(o => ({ ...o, text: o.value === 'unknown' ? 'Not stated' : countryName(o.value) }));
            panel.innerHTML = `<div class="popover-title label">Region</div>
                <div class="popover-body">
                    <div class="popover-title label">Regions</div>
                    ${groups.map((g, i) => checkbox(`opt-group-${i}`, g.text, undefined, filters.region_group.includes(g.value))).join('')}
                    <div class="popover-title label">Countries</div>
                    ${countries.length ? countries.map((c, i) => checkbox(`opt-country-${i}`, c.text, c.count, filters.country.includes(c.value))).join('') : '<div class="option-empty">Loading…</div>'}
                </div>${footer()}`;
            $$('[id^="opt-group-"]', panel).forEach((button, i) => button.onclick = () => toggleValue('region_group', groups[i].value));
            $$('[id^="opt-country-"]', panel).forEach((button, i) => button.onclick = () => toggleValue('country', countries[i].value));
            arrowKeys(panel);
            wireFooter(panel, close, () => { clearKey('region_group'); clearKey('country'); });
            return panel.querySelector('.option');
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
    $('#clearFilters').addEventListener('click', () => { clearFilters(); $('#searchInput').value = ''; });

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

    on('filters', () => { announceNext = true; paintAll(); refreshFacetsSoon(); });
    on('active-tab', () => refreshFacetsSoon());
    paintAll();
    refreshFacets();
}
