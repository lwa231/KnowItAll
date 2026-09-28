// Turns a PaneData into HTML: the postings table, its loading skeleton, and the empty/error states.
import { state } from './state.js';
import { filters, isFiltering } from './filters.js';
import { SERVER_SORT } from './jobs.js';
import { esc, relTime, fmtNum } from './util.js';

const WORK = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site' };
const TYPE = { full_time: 'Full-time', part_time: 'Part-time', contract: 'Contract', intern: 'Intern', other: 'Other' };

// Title takes a share of the width; the short columns (Work, Type, Posted) have a fixed width in pixels so their
// headers never clip; whatever is left is shared by Company, Location and Department (which have no width).
// The Company column only exists when several companies share the table. Columns drop out as a pane narrows
// (see table.css, thresholds set in workspace.js).
const COLUMNS = [
    { key: 'title',      label: 'Title',      w: ['30%', '38%'], sort: 'title' },
    { key: 'company',    label: 'Company',    w: ['', ''],       sort: 'company',  cls: 'col-company', onlyAll: true },
    { key: 'location',   label: 'Location',   w: ['', ''],       sort: 'location', cls: 'col-loc' },
    { key: 'workplace',  label: 'Work',       w: ['92px', '92px'], cls: 'col-work' },
    { key: 'type',       label: 'Type',       w: ['96px', '96px'], cls: 'col-type' },
    { key: 'department', label: 'Department', w: ['', ''],       cls: 'col-dept' },
    { key: 'when',       label: 'Posted',     w: ['104px', '104px'], sort: 'posted', cls: 'col-posted right' },
];

const columnsFor = key => COLUMNS.filter(c => key === 'ALL' || !c.onlyAll);
const WHEN_LABEL = { current: 'Posted', missing: 'Gone since', closed: 'Closed' };

function whenCell(job) {
    if (job.status === 'closed') return relTime(job.closed_at);
    if (job.status === 'missing') return relTime(job.missing_since);
    return relTime(job.posted);
}

export function rowHTML(job, key) {
    const gone = job.status && job.status !== 'open';
    const title = job.url
        ? `<a class="job-title" href="${esc(job.url)}" target="_blank" rel="noopener noreferrer">${esc(job.title)}</a>`
        : `<span class="job-title">${esc(job.title)}</span>`;
    const cells = columnsFor(key).map(c => {
        switch (c.key) {
            case 'title': return `<td><span class="title-cell"><span class="badge-new">${job.is_new ? '<span class="badge badge-new">new<span class="sr-only"> posting</span></span>' : ''}</span>${title}</span></td>`;
            case 'company': return `<td class="${c.cls}" title="${esc(job.company || job.domain)}">${esc(job.company || job.domain || '—')}</td>`;
            case 'location': return `<td class="${c.cls}" title="${esc(job.location || '')}">${esc(job.location || '—')}</td>`;
            case 'workplace': return `<td class="${c.cls}">${job.workplace ? `<span class="badge ${job.workplace === 'remote' ? 'badge-remote' : ''}">${WORK[job.workplace]}</span>` : '<span class="dim" aria-label="not stated">—</span>'}</td>`;
            case 'type': return `<td class="${c.cls}">${job.employment_type ? esc(TYPE[job.employment_type] || job.employment_type) : '<span class="dim" aria-label="not stated">—</span>'}</td>`;
            case 'department': return `<td class="${c.cls}" title="${esc(job.department || '')}">${esc(job.department || '—')}</td>`;
            default: return `<td class="${c.cls}">${esc(whenCell(job))}</td>`;
        }
    }).join('');
    return `<tr class="row${gone ? ' gone' : ''}">${cells}</tr>`;
}

function headHTML(pane) {
    const all = pane.key === 'ALL';
    const cells = columnsFor(pane.key).map(c => {
        const width = c.w[all ? 0 : 1];
        const style = width ? ` style="width:${width}"` : '';
        const label = c.key === 'when' ? WHEN_LABEL[filters.status] : c.label;
        const cls = c.cls || '';
        if (!c.sort) return `<th scope="col" class="${cls}"${style}><span class="th-inner">${esc(label)}</span></th>`;
        const active = pane.sortKey === c.sort;
        const aria = active ? (pane.sortDir === 'asc' ? 'ascending' : 'descending') : 'none';
        const caret = active ? (pane.sortDir === 'asc' ? '↑' : '↓') : '↕';
        const sortable = SERVER_SORT[c.sort];
        return `<th scope="col" class="${cls}"${style} aria-sort="${aria}">
            <button type="button" class="th-inner" data-sort="${sortable}" data-pane="${esc(pane.key)}">${esc(label)}<span class="sort-caret" aria-hidden="true">${caret}</span><span class="sr-only">, sort</span></button></th>`;
    }).join('');
    return `<thead><tr>${cells}</tr></thead>`;
}

export function skeletonHTML(key) {
    const cols = columnsFor(key).length;
    const widths = [70, 40, 55, 30, 30, 40, 25];
    const row = i => `<tr class="skeleton">${Array.from({ length: cols }, (_, c) => `<td><span class="bar" style="width:${widths[(c + i) % widths.length]}%"></span></td>`).join('')}</tr>`;
    return `<table class="data-table" aria-busy="true"><tbody>${Array.from({ length: 8 }, (_, i) => row(i)).join('')}</tbody></table>`;
}

/** The list's footer sits after the table, not inside it: a colspan row would add phantom columns whenever
 *  some columns are hidden on a narrow pane. */
export function footerHTML(pane) {
    let inner;
    if (pane.loadingMore) inner = 'Loading more…';
    else if (pane.hasMore) inner = `<button type="button" data-load-more="${esc(pane.key)}">Load more</button> <span aria-hidden="true">·</span> ${fmtNum(pane.rows.length)} of ${fmtNum(pane.total)}`;
    else inner = `${fmtNum(pane.total)} ${pane.total === 1 ? 'posting' : 'postings'}`;
    return `<div class="load-row" data-sentinel="${esc(pane.key)}">${inner}</div>`;
}

function emptyHTML(pane) {
    const status = filters.status;
    if (pane.error) {
        return `<div class="empty" role="alert"><div class="headline">Could not load postings</div><p>${esc(pane.error)}</p>
                <button type="button" class="btn" data-retry="${esc(pane.key)}">Try again</button></div>`;
    }
    if (isFiltering()) {
        return `<div class="empty"><div class="headline">No postings match these filters</div>
                <p>Try removing a filter or shortening the search. Words are prefixes, so "eng" finds "engineer".</p>
                <button type="button" class="btn" data-clear-filters>Clear filters</button></div>`;
    }
    if (status !== 'current') {
        const gone = status === 'missing';
        return `<div class="empty"><div class="headline">${gone ? 'Nothing has gone missing yet' : 'Nothing has closed yet'}</div>
                <p>${gone
                    ? 'A posting is marked gone when a complete scan no longer lists it.'
                    : 'A posting is marked closed when two complete scans in a row no longer list it.'}
                   Scan the company again later to find out.</p></div>`;
    }
    if (pane.key === 'ALL' && !state.companies.length) {
        return `<div class="empty"><div class="headline">Nothing scanned yet</div><p>Enter a company web address above and press Start.</p></div>`;
    }
    if (state.running) return `<div class="empty"><div class="headline">Scanning…</div><p>Postings appear here as they are found.</p></div>`;
    return `<div class="empty"><div class="headline">No postings yet</div><p>This company's scan found no postings.</p></div>`;
}

/** The whole body of a pane: table, skeleton, or an empty/error message. */
export function bodyHTML(pane) {
    if (!pane.loadedOnce && pane.loading) return skeletonHTML(pane.key);
    if (pane.error && !pane.rows.length) return emptyHTML(pane);
    if (!pane.rows.length) return pane.loadedOnce ? emptyHTML(pane) : skeletonHTML(pane.key);
    return `<table class="data-table">${headHTML(pane)}<tbody>${pane.rows.map(job => rowHTML(job, pane.key)).join('')}</tbody></table>${footerHTML(pane)}`;
}
