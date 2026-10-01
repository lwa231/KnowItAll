// How many postings match the filters, overall and per company, next to how many there are in all.
// Filters hide non-matching postings everywhere, so every number on screen (queue rows, the header, the banner) has to
// be a matching count; "of N" is what there would be with no filters. Answers come from /api/jobs with only the cheap
// per-company facet, and are throttled while a scan streams rows in.
import * as api from './api.js';
import { state, emit } from './state.js';
import { filters, toParams, unfiltered, isFiltering } from './filters.js';
import { throttle } from './util.js';

export const counts = { hidden: {}, ready: false, filtering: false, matching: 0, total: 0, matchByDomain: {}, totalByDomain: {}, newByDomain: {} };

const perDomain = data => Object.fromEntries((data.facets?.domain || []).map(f => [f.value, f.count]));

export async function refreshCounts() {
    const filtering = isFiltering();
    try {
        const matched = await api.get(`/api/jobs?${toParams({ limit: 1, facets: 'domain' })}`);
        counts.matching = matched.total; counts.matchByDomain = perDomain(matched);
        if (filtering) {
            const [all, fresh] = await Promise.all([
                api.get(`/api/jobs?${toParams({ limit: 1, facets: 'domain' }, unfiltered())}`),
                api.get(`/api/jobs?${toParams({ limit: 1, facets: 'domain' }, { ...filters, new_only: true })}`),
            ]);
            counts.total = all.total; counts.totalByDomain = perDomain(all); counts.newByDomain = perDomain(fresh);
        } else {
            counts.total = matched.total; counts.totalByDomain = counts.matchByDomain;
            counts.newByDomain = Object.fromEntries(state.companies.map(c => [c.domain, c.new_count || 0]));
        }
        counts.filtering = filtering; counts.ready = true;
    } catch { return; }                                       // keep the last answer; the panes show their own errors
    emit('counts');
}
export const refreshCountsLive = throttle(refreshCounts, 2500);      // while a scan is streaming rows in

/** Matches of one company, or null until the first answer (callers then show the plain count). */
export const matchesOf = domain => counts.ready && counts.filtering ? (counts.matchByDomain[domain] || 0) : null;
export const newMatchesOf = company => counts.ready && counts.filtering ? (counts.newByDomain[company.domain] || 0) : (company.new_count || 0);
