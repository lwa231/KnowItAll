// Postings for one pane: the rows loaded so far, the server's total, sorting and paging.
// The server does the filtering, sorting and counting (/api/jobs); this just asks for pages and keeps them.
import * as api from './api.js';
import { state, emit } from './state.js';
import { toParams } from './filters.js';

export const PAGE = 100;
export const MAX_LOADED_FOR_LIVE_REFRESH = 500;      // /api/jobs pages up to 500 rows; beyond that only the total is refreshed
export const SERVER_SORT = { title: 'title', company: 'company', location: 'location', posted: 'posted' };

const registry = new Map();

export class PaneData {
    constructor(key) {
        this.key = key;                  // 'ALL' or a domain
        this.rows = [];
        this.total = 0;
        this.loading = false;
        this.loadingMore = false;
        this.error = null;
        this.loadedOnce = false;
        this.sortKey = null;             // null = the server's default (newest posted first)
        this.sortDir = 'desc';
        this.seq = 0;                    // ignore answers to questions that have since been replaced
        this.resetScroll = false;        // a new question (filter, sort) starts at the top; a live refresh keeps your place
    }

    get domain() { return this.key === 'ALL' ? null : this.key; }
    get hasMore() { return this.rows.length < this.total; }

    params(offset, limit) {
        const extra = { domain: this.domain, run_id: this.domain ? state.runView[this.domain] : null, limit, offset, facets: 0 };
        if (this.sortKey) { extra.sort = SERVER_SORT[this.sortKey]; extra.order = this.sortDir; }
        return toParams(extra);
    }

    /** Fetch again from the top. `live` keeps what is on screen while the answer is on its way (no flicker). */
    async refresh({ live = false } = {}) {
        const seq = ++this.seq;
        const limit = live && this.loadedOnce
            ? Math.min(MAX_LOADED_FOR_LIVE_REFRESH, Math.max(PAGE, this.rows.length))
            : PAGE;
        if (!live) { this.loading = true; this.error = null; this.resetScroll = true; emit('pane-data', this.key); }
        try {
            const data = await api.get(`/api/jobs?${this.params(0, limit)}`);
            if (seq !== this.seq) return;
            if (!live || this.rows.length <= MAX_LOADED_FOR_LIVE_REFRESH) this.rows = data.jobs;   // a very long list keeps its scroll; only the count moves
            this.total = data.total;
            this.loadedOnce = true;
            this.error = null;
        } catch (error) {
            if (seq !== this.seq) return;
            this.error = error.message || 'could not load';
        } finally {
            if (seq === this.seq) { this.loading = false; emit('pane-data', this.key); }
        }
    }

    async loadMore() {
        if (this.loadingMore || this.loading || !this.hasMore) return;
        const seq = this.seq;
        this.loadingMore = true;
        emit('pane-more', { key: this.key, state: 'loading' });
        try {
            const data = await api.get(`/api/jobs?${this.params(this.rows.length, PAGE)}`);
            if (seq !== this.seq) return;
            const fresh = data.jobs;
            this.total = data.total;
            this.rows = this.rows.concat(fresh);
            emit('pane-more', { key: this.key, state: 'appended', rows: fresh });
        } catch (error) {
            if (seq === this.seq) { this.error = error.message; emit('pane-data', this.key); }
        } finally {
            this.loadingMore = false;
            if (seq === this.seq) emit('pane-more', { key: this.key, state: 'idle' });
        }
    }

    /** Header click: default -> ascending -> descending -> default. */
    cycleSort(key) {
        if (this.sortKey !== key) { this.sortKey = key; this.sortDir = 'asc'; }
        else if (this.sortDir === 'asc') this.sortDir = 'desc';
        else { this.sortKey = null; this.sortDir = 'desc'; }
        return this.refresh();
    }
}

export function paneData(key) {
    if (!registry.has(key)) registry.set(key, new PaneData(key));
    return registry.get(key);
}
export const allPaneData = () => [...registry.values()];
export function forgetPaneData(key) { registry.delete(key); }
export function resetPaneDataForTests() { registry.clear(); }
