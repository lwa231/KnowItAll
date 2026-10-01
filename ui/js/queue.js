// The sidebar queue: one row per company with its state, counts, and a Stop button while it is running.
//
// Rows are built once and then updated in place. Rebuilding the list with innerHTML on every server message (several a
// second during a scan) dropped keyboard focus from whatever the person had tabbed to, e.g. a Stop button.
// A second line appears only when something needs saying: what a scanning company is doing now, or why a finished one
// has nothing ("blocked", "no careers page", "3 min limit").
import * as api from './api.js';
import { state, byDomain, totalJobs, on } from './state.js';
import { matchesOf } from './counts.js';
import { showCompany } from './workspace.js';
import { glyphOf, isFinished, waitingText } from './outcomes.js';
import { $, esc, fmtNum, svgIcon } from './util.js';

const rows = new Map();                                   // domain -> its <li>

const setText = (el, text) => { if (el.textContent !== text) el.textContent = text; };

function build(domain) {
    const li = document.createElement('li');
    li.className = 'queue-item';
    li.innerHTML = `<button type="button" class="queue-main" data-open="${esc(domain)}">
            <span class="queue-state" data-glyph aria-hidden="true"></span>
            <span class="queue-name truncate">${esc(domain)}</span>
            <span class="sr-only" data-sr></span>
            <span class="queue-meta" aria-hidden="true"><span class="badge badge-warn" data-gone hidden></span><span data-count></span></span>
            <span class="queue-reason truncate" data-reason aria-hidden="true" hidden></span>
        </button>
        <button type="button" class="icon-btn danger" data-stop="${esc(domain)}" aria-label="Stop ${esc(domain)}" title="Stop this company" hidden>${svgIcon('stop')}</button>`;
    return li;
}

/** The short second line and its tone, or null when the row needs none. */
function reasonOf(company) {
    if (company.state === 'scanning') return { text: company.phase || 'Working…', tone: 'dim' };
    if (company.state === 'waiting') return { text: waitingText(company), tone: 'dim' };
    if (company.state === 'ready') return { text: 'ready', tone: 'dim' };
    if (!isFinished(company)) return null;
    return company.outcome_short ? { text: company.outcome_short, tone: glyphOf(company).tone } : null;
}

/** The postings count: "12 / 340" (matching / found) while filters are on, plain "340" otherwise. */
function countText(company) {
    const matches = matchesOf(company.domain);
    return matches === null ? fmtNum(company.jobs_count) : `${fmtNum(matches)} / ${fmtNum(company.jobs_count)}`;
}

function paint(li, company) {
    const { glyph, tone } = glyphOf(company);
    const glyphEl = li.querySelector('[data-glyph]');
    setText(glyphEl, glyph);
    const glyphClass = `queue-state ${tone}`;
    if (glyphEl.className !== glyphClass) glyphEl.className = glyphClass;

    const gone = (company.missing_count || 0) + (company.closed_count || 0);
    const goneEl = li.querySelector('[data-gone]');
    goneEl.hidden = !gone;
    if (gone) { setText(goneEl, `−${gone}`); goneEl.title = `${gone} no longer listed`; }

    const count = company.state === 'ready' ? 'ready' : company.state === 'waiting' ? 'waiting' : company.state === 'scanning' ? `… ${countText(company)}` : countText(company);
    setText(li.querySelector('[data-count]'), count);

    const reason = reasonOf(company);
    const reasonEl = li.querySelector('[data-reason]');
    reasonEl.hidden = !reason;
    if (reason) {
        setText(reasonEl, reason.text);
        const reasonClass = `queue-reason truncate ${reason.tone}`;
        if (reasonEl.className !== reasonClass) reasonEl.className = reasonClass;
        reasonEl.title = company.outcome_detail && isFinished(company) ? company.outcome_detail : reason.text;
    }
    li.querySelector('.queue-main').classList.toggle('has-reason', !!reason);

    // One complete phrase for screen readers, instead of the glyph and numbers read separately.
    const counted = company.state === 'ready' || company.state === 'waiting' ? '' : `, ${count} postings`;
    setText(li.querySelector('[data-sr]'), `, ${company.state}${counted}${reason ? `, ${reason.text}` : ''}`);

    const busy = company.state === 'scanning' || company.state === 'waiting';
    li.querySelector('[data-stop]').hidden = !busy;
    li.classList.toggle('selected', state.activeTab === company.domain);
}

export function renderQueue() {
    const list = $('#queueList');
    if (!state.companies.length) {
        rows.clear();
        if (!list.querySelector('.queue-empty')) list.innerHTML = '<li class="queue-empty">Nothing queued yet.</li>';
    } else {
        list.querySelector('.queue-empty')?.remove();
        const present = new Set();
        state.companies.forEach((company, index) => {
            let li = rows.get(company.domain);
            if (!li || !li.isConnected) { li = build(company.domain); rows.set(company.domain, li); }
            paint(li, company);
            // Put it in its place only when it is not already there: moving a node drops focus from what is inside it.
            if (list.children[index] !== li) list.insertBefore(li, list.children[index] || null);
            present.add(company.domain);
        });
        for (const [domain, li] of rows) if (!present.has(domain)) { li.remove(); rows.delete(domain); }
    }
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
            document.querySelector('.rail-item[data-view="scraper"]').click();
            showCompany(open.dataset.open);
            renderQueue();
        }
    });
    on('active-tab', renderQueue);
    on('counts', renderQueue);
    on('filters', renderQueue);                        // the selected row follows the tab, whoever changed it
}
