// How a scan ended, as the window shows it: a glyph and a tone for the sidebar, the words for an empty pane, and the
// "Companies with no listings" list. The server decides the outcome and writes the wording (knowitall/outcomes.py, so
// the command line says the same thing); this only chooses how to display it. Never colour alone: every state has a
// glyph and words.
import { state, byDomain } from './state.js';
import { esc, safeHref, fmtClock } from './util.js';

// glyph, tone class (base.css), and whether the outcome is "nothing to read" (muted) or needs attention.
const OUTCOME_UI = {
    found:           { glyph: '●', tone: 'good' },
    no_listings:     { glyph: '–', tone: 'muted' },
    no_careers_page: { glyph: '–', tone: 'muted' },
    unsupported:     { glyph: '–', tone: 'muted' },
    blocked:         { glyph: '!', tone: 'caution' },
    unreachable:     { glyph: '!', tone: 'caution' },
    timed_out:       { glyph: '!', tone: 'caution' },
    stopped:         { glyph: '■', tone: 'caution' },
    error:           { glyph: '×', tone: 'bad' },
};
const STATE_UI = {
    scanning: { glyph: '◐', tone: 'accent' }, queued: { glyph: '○', tone: 'dim' }, done: { glyph: '●', tone: 'good' },
    failed: { glyph: '×', tone: 'bad' }, stopped: { glyph: '■', tone: 'caution' },
};
const FINISHED = ['done', 'failed', 'stopped'];

export const isFinished = company => FINISHED.includes(company.state);
export const glyphOf = company => (isFinished(company) && OUTCOME_UI[company.outcome]) || STATE_UI[company.state] || STATE_UI.queued;

/** Finished, and nothing to show for it: these are the companies the All pane lists below its table. */
export const emptyCompanies = () => state.companies.filter(c => isFinished(c) && !(c.jobs_count > 0));

/** "tesla.com blocked automated access." -> "Blocked automated access." (the domain is already in its own column). */
export function withoutDomain(message, domain) {
    const text = String(message || '');
    if (!domain || !text.toLowerCase().startsWith(domain.toLowerCase())) return text;
    const rest = text.slice(domain.length).trimStart();
    return rest ? rest[0].toUpperCase() + rest.slice(1) : text;
}

export function careersLinkHTML(company) {
    const href = safeHref(company.careers_url);
    if (!href) return '';
    return `<a class="btn" href="${esc(href)}" target="_blank" rel="noopener noreferrer">Open careers page<span class="sr-only"> (opens in your browser)</span></a>`;
}

/** The empty pane of one company: what it is doing now, or how its scan ended and what to do about it. */
export function companyEmptyHTML(company) {
    if (company.state === 'scanning') {
        return `<div class="empty"><div class="headline">Scanning…</div>
            <p class="phase"><span data-phase="${esc(company.domain)}">${esc(company.phase || 'Working…')}</span>
               <span class="dim" aria-hidden="true">·</span> <span class="clock" data-elapsed="${esc(company.started_at || '')}">${fmtClock(company.started_at ? Date.now() / 1000 - company.started_at : 0)}</span></p>
            <p class="dim">Postings appear here as they are found.</p></div>`;
    }
    if (company.state === 'queued') {
        return `<div class="empty"><div class="headline">${state.running ? 'Waiting to scan' : 'Not scanned yet'}</div>
            <p>${state.running ? 'This company starts when a scan slot is free.' : 'Press Start to scan it.'}</p></div>`;
    }
    if (!company.outcome_detail) {                               // a scan from before outcomes were recorded
        return `<div class="empty"><div class="headline">No postings yet</div><p>This company's scan found no postings.</p></div>`;
    }
    const link = careersLinkHTML(company);
    return `<div class="empty"><div class="headline">${esc(company.outcome_detail)}</div>
        ${company.outcome_hint ? `<p>${esc(company.outcome_hint)}</p>` : ''}
        ${company.careers_note ? `<p class="dim">${esc(company.careers_note)}</p>` : ''}
        ${link ? `<div class="empty-actions">${link}</div>` : ''}</div>`;
}

/** The compact list under the All table: one line for each company that finished with nothing to show. */
export function noListingsHTML() {
    const list = emptyCompanies();
    if (!list.length) return '';
    const rows = list.map(c => {
        const { glyph, tone } = glyphOf(c);
        const message = c.outcome_detail ? withoutDomain(c.outcome_detail, c.domain) : 'No postings found.';
        const link = careersLinkHTML(c);
        return `<li class="nl-row">
            <span class="nl-glyph ${tone}" aria-hidden="true">${glyph}</span>
            <button type="button" class="row-link nl-name" data-open-company="${esc(c.domain)}">${esc(c.domain)}</button>
            <span class="nl-message truncate" title="${esc(c.outcome_detail || '')}">${esc(message)}</span>
            ${link ? `<span class="nl-action">${link.replace('class="btn"', 'class="btn btn-quiet"')}</span>` : ''}</li>`;
    }).join('');
    return `<section class="no-listings" aria-labelledby="noListingsTitle">
        <h3 class="label" id="noListingsTitle">Companies with no listings (${list.length})</h3><ul>${rows}</ul></section>`;
}

/** The scanning text and clocks in open panes, updated in place (no re-render, so nothing flickers or loses focus). */
export function paintPhases(root = document) {
    root.querySelectorAll('[data-phase]').forEach(el => { el.textContent = byDomain(el.dataset.phase)?.phase || 'Working…'; });
}
export function tickClocks(root = document) {
    root.querySelectorAll('[data-elapsed]').forEach(el => {
        if (el.dataset.elapsed) el.textContent = fmtClock(Date.now() / 1000 - Number(el.dataset.elapsed));
    });
}
