// History: every earlier scan. Choosing one opens that scan's postings in the workspace.
import * as api from '../api.js';
import { openCompany } from '../workspace.js';
import { $, esc, relTime, fmtNum } from '../util.js';

const STATUS = { done: ['complete', ''], stopped: ['stopped', 'badge-warn'], failed: ['failed', 'badge-bad'], running: ['running', ''] };

export async function loadHistory() {
    const body = $('#historyBody');
    try {
        const { runs } = await api.get('/api/history');
        body.innerHTML = runs.length ? runs.map(r => {
            const [text, cls] = STATUS[r.status] || [r.status, ''];
            return `<tr>
                <td><button type="button" class="row-link" data-run="${r.id}" data-domain="${esc(r.domain)}" aria-label="Open the ${esc(r.domain)} scan from ${esc(relTime(r.finished_at || r.started_at))}">${esc(r.domain)}</button></td>
                <td class="muted">${esc(r.source || '—')}</td>
                <td class="muted">${fmtNum(r.jobs_count)}</td>
                <td class="good">${fmtNum(r.new_count)}</td>
                <td><span class="badge ${cls}">${esc(text)}</span></td>
                <td class="right dim">${relTime(r.finished_at || r.started_at)}</td></tr>`;
        }).join('') : '<tr><td colspan="6" class="dim">No previous scans yet.</td></tr>';
    } catch { body.innerHTML = '<tr><td colspan="6" class="bad">Could not load history.</td></tr>'; }
}

export function initHistory(showView) {
    $('#historyBody').addEventListener('click', event => {
        const link = event.target.closest('[data-run]');
        if (!link) return;
        showView('scraper');
        openCompany(link.dataset.domain, { runId: link.dataset.run });
    });
}
