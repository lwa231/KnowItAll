// The sidebar queue: one row per company with its state, counts, and a Stop button while it is running.
import * as api from './api.js';
import { state, byDomain, totalJobs } from './state.js';
import { showCompany } from './workspace.js';
import { $, esc, fmtNum, svgIcon } from './util.js';

const GLYPH = { done: '●', scanning: '◐', queued: '○', failed: '×', stopped: '■' };
const GLYPH_CLASS = { done: 'good', scanning: 'accent', queued: 'dim', failed: 'bad', stopped: 'caution' };

export function renderQueue() {
    const list = $('#queueList');
    const focused = document.activeElement?.closest?.('[data-open]')?.dataset.open;
    if (!state.companies.length) {
        list.innerHTML = '<li class="queue-empty">Nothing queued yet.</li>';
    } else {
        list.innerHTML = state.companies.map(c => {
            const gone = (c.missing_count || 0) + (c.closed_count || 0);
            const count = c.state === 'queued' ? 'queued' : c.state === 'scanning' ? `… ${fmtNum(c.jobs_count)}` : fmtNum(c.jobs_count);
            const busy = c.state === 'scanning' || (c.state === 'queued' && state.running);
            return `<li class="queue-item${state.activeTab === c.domain ? ' selected' : ''}">
                <button type="button" class="queue-main" data-open="${esc(c.domain)}">
                    <span class="queue-state ${GLYPH_CLASS[c.state] || 'dim'}" aria-hidden="true">${GLYPH[c.state] || '○'}</span>
                    <span class="queue-name truncate">${esc(c.domain)}</span>
                    <span class="sr-only">, ${esc(c.state)}, ${esc(count)} postings</span>
                    <span class="queue-meta" aria-hidden="true">
                        ${gone ? `<span class="badge badge-warn" title="${gone} no longer listed">−${gone}</span>` : ''}
                        <span>${esc(count)}</span></span>
                </button>
                ${busy ? `<button type="button" class="icon-btn danger" data-stop="${esc(c.domain)}" aria-label="Stop ${esc(c.domain)}" title="Stop this company">${svgIcon('stop')}</button>` : ''}
            </li>`;
        }).join('');
    }
    if (focused) list.querySelector(`[data-open="${CSS.escape(focused)}"]`)?.focus();
    const done = state.companies.filter(c => c.state === 'done').length;
    $('#queueCount').textContent = `${done}/${state.companies.length}`;
    $('#sessionLine').textContent = `${state.companies.length} ${state.companies.length === 1 ? 'company' : 'companies'} · ${fmtNum(totalJobs())} jobs`;
}

export function initQueue() {
    $('#queueList').addEventListener('click', async event => {
        const stop = event.target.closest('[data-stop]');
        if (stop) { await api.post('/api/stop', { domain: stop.dataset.stop }).catch(() => {}); return; }
        const open = event.target.closest('[data-open]');
        if (open && byDomain(open.dataset.open)) {
            document.querySelector('.nav-item[data-view="scraper"]').click();
            showCompany(open.dataset.open);
            renderQueue();
        }
    });
}
