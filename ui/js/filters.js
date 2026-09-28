// The global filter state and how it becomes a query. No DOM here: filterbar.js draws it, jobs.js uses it.
import { emit } from './state.js';

export const LIST_KEYS = ['workplace', 'employment_type', 'country', 'region_group', 'department', 'source'];
export const STATUSES = ['current', 'missing', 'closed'];

const blank = () => ({
    q: '', workplace: [], employment_type: [], country: [], region_group: [], department: [], source: [],
    posted_within_days: null, new_only: false, status: 'current',
});

export const filters = blank();

/** Query string for /api/jobs from the current filters, plus per-call extras (domain, run_id, sort, paging...). */
export function toParams(extra = {}, source = filters) {
    const params = new URLSearchParams();
    if (source.q) params.set('q', source.q);
    LIST_KEYS.forEach(key => source[key].forEach(value => params.append(key, value)));
    if (source.posted_within_days) params.set('posted_within_days', source.posted_within_days);
    if (source.new_only) params.set('new_only', '1');
    if (source.status !== 'current') params.set('status', source.status);
    Object.entries(extra).forEach(([key, value]) => { if (value !== null && value !== undefined && value !== '') params.set(key, value); });
    return params;
}

/** How many separate things are narrowing the results (search text counts as one; the status switch does not). */
export function activeCount(source = filters) {
    return LIST_KEYS.filter(key => source[key].length).length
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

export function resetForTests() { Object.assign(filters, blank()); }
