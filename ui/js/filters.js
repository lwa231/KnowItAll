// The global filter state and how it becomes a query. No DOM here: filterbar.js draws it, jobs.js uses it.
import * as api from './api.js';
import { emit } from './state.js';
import { debounce } from './util.js';

export const LIST_KEYS = ['workplace', 'employment_type', 'country', 'region_group', 'department', 'source'];
export const STATUSES = ['current', 'missing', 'closed'];

const blank = () => ({
    q: '', workplace: [], employment_type: [], country: [], region_group: [], department: [], source: [],
    posted_within_days: null, new_only: false, status: 'current',
});

export const filters = blank();

/** The part of the filters that is saved between launches (the chip filters). Search text, "New only" and the
 *  Listed/Gone/Closed switch are this-visit choices: they reset, so the app never opens to an unexplained empty feed. */
export const PERSISTED_KEYS = [...LIST_KEYS, 'posted_within_days'];
export const profileOf = (source = filters) => Object.fromEntries(
    PERSISTED_KEYS.filter(key => key === 'posted_within_days' ? source[key] : source[key].length).map(key => [key, source[key]]));

/** The filters as they were saved last time, applied without saving them again. */
export function applySaved(profile) {
    Object.assign(filters, blank());
    for (const key of PERSISTED_KEYS) {
        if (profile?.[key] === undefined) continue;
        filters[key] = key === 'posted_within_days' ? profile[key] : [...profile[key]];
    }
    savedSignature = JSON.stringify(profileOf());
}

let savedSignature = JSON.stringify({});
const save = debounce(async () => {
    const profile = profileOf(), signature = JSON.stringify(profile);
    if (signature === savedSignature) return;
    try { await api.post('/api/filters', { filters: profile }); savedSignature = signature; }
    catch { /* the filters still work for this visit; the next change tries to save again */ }
}, 600);
export const saveFiltersSoon = () => save();

/** The same filters with nothing narrowing them (keeps the Listed/Gone/Closed choice): what "of 1,204" counts. */
export const unfiltered = () => ({ ...blank(), status: filters.status });

const UNKNOWN = 'unknown';
/** The values that narrow results for one list filter. "Include postings that don't say" (unknown) only widens a
 *  choice that was made, so on its own it narrows nothing. The Region chip holds region groups and countries together. */
function effective(key, source) {
    const values = source[key];
    const chosen = values.filter(v => v !== UNKNOWN).length;
    if (key === 'country') return chosen || source.region_group.length ? values : [];
    if (key === 'region_group') return values;
    return chosen ? values : [];
}

/** Query string for /api/jobs from the current filters, plus per-call extras (domain, run_id, sort, paging...). */
export function toParams(extra = {}, source = filters) {
    const params = new URLSearchParams();
    if (source.q) params.set('q', source.q);
    LIST_KEYS.forEach(key => effective(key, source).forEach(value => params.append(key, value)));
    if (source.posted_within_days) params.set('posted_within_days', source.posted_within_days);
    if (source.new_only) params.set('new_only', '1');
    if (source.status !== 'current') params.set('status', source.status);
    Object.entries(extra).forEach(([key, value]) => { if (value !== null && value !== undefined && value !== '') params.set(key, value); });
    return params;
}

/** How many separate things are narrowing the results (search text counts as one; the status switch does not). */
export function activeCount(source = filters) {
    return LIST_KEYS.filter(key => effective(key, source).length).length
        + (source.posted_within_days ? 1 : 0) + (source.new_only ? 1 : 0) + (source.q ? 1 : 0);
}
export const isFiltering = (source = filters) => activeCount(source) > 0;

export function setFilters(patch) {
    Object.assign(filters, patch);
    emit('filters');
}

export function toggleValue(key, value) {
    const list = filters[key];
    const at = list.indexOf(value);
    if (at >= 0) list.splice(at, 1); else list.push(value);
    emit('filters');
}

export function clearKey(key) {
    if (LIST_KEYS.includes(key)) filters[key] = [];
    else if (key === 'posted_within_days') filters.posted_within_days = null;
    else if (key === 'new_only') filters.new_only = false;
    else if (key === 'q') filters.q = '';
    emit('filters');
}

/** Drop every narrowing filter. The status switch (Listed / Gone / Closed) is a choice of view, so it stays. */
export function clearFilters() {
    const status = filters.status;
    Object.assign(filters, blank(), { status });
    emit('filters');
}

export function resetForTests() { Object.assign(filters, blank()); savedSignature = JSON.stringify({}); }
