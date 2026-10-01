// History: this session's scans (earlier sessions live in backups). Choosing one opens that scan's postings.
import * as api from '../api.js';
import { state, emit } from '../state.js';
import { openCompany } from '../workspace.js';
import { filters, toParams, isFiltering } from '../filters.js';
import { $, esc, relTime, fmtNum } from '../util.js';

const ENDED = { found: ['', ''], no_listings: ['no listings', 'badge-warn'], no_careers_page: ['no careers page', 'badge-warn'],
    unreachable: ['couldn\'t reach', 'badge-warn'], blocked: ['blocked', 'badge-warn'], unsupported: ['unsupported platform', 'badge-warn'],
    timed_out: ['time limit', 'badge-warn'], stopped: ['stopped', 'badge-warn'], error: ['error', 'badge-bad'] };
const STATUS = { done: ['complete', ''], stopped: ['stopped', 'badge-warn'], failed: ['failed', 'badge-bad'], running: ['running', ''], interrupted: ['interrupted', 'badge-warn'] };

export async function loadHistory() {
    const body = $('#historyBody');
    const narrowed = isFiltering();
    $('#matchesHead').hidden = !narrowed;
    const columns = narrowed ? 7 : 6;
    try {
        const params = toParams({ session: 'current' }, narrowed ? filters : { q: '', workplace: [], employment_type: [], country: [], region_group: [], department: [], source: [], posted_within_days: null, new_only: false, status: 'current' });
        const { runs } = await api.get(`/api/history?${params}`);
        body.innerHTML = runs.length ? runs.map(r => {
            const [text, cls] = (r.outcome && r.outcome !== 'found' ? ENDED[r.outcome] : null) || (STATUS[r.status] || [r.status, '']);
            return `<tr>
                <td><button type="button" class="row-link" data-run="${r.id}" data-domain="${esc(r.domain)}" aria-label="Open the ${esc(r.domain)} scan from ${esc(relTime(r.finished_at || r.started_at))}">${esc(r.domain)}</button></td>
                <td class="muted">${esc(r.source || '—')}</td>
                <td class="muted">${fmtNum(r.jobs_count)}</td>
                ${narrowed ? `<td class="bright">${fmtNum(r.matches || 0)}</td>` : ''}
                <td class="good">${fmtNum(r.new_count)}</td>
                <td>${text ? `<span class="badge ${cls}" title="${esc(r.outcome_detail || '')}">${esc(text)}</span>` : '<span class="dim">found postings</span>'}</td>
                <td class="right dim">${relTime(r.finished_at || r.started_at)}</td></tr>`;
        }).join('') : `<tr><td colspan="${columns}" class="dim">No scans yet this session.</td></tr>`;
    } catch { body.innerHTML = `<tr><td colspan="${columns}" class="bad">Could not load history.</td></tr>`; }
}

export function initHistory(showView) {
    $('#historyBody').addEventListener('click', event => {
        const link = event.target.closest('[data-run]');
        if (!link) return;
        showView('scraper');
        openCompany(link.dataset.domain, { runId: link.dataset.run });
        state.openedFrom = 'history';                        // after the tab change, which clears it: the breadcrumbs say "History ›"
        emit('run-view', link.dataset.domain);
    });
}
