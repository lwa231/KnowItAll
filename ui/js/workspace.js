// The workspace: a tab strip, four quadrant tiles (the "dock") and floating windows. Each pane shows the
// postings of one company (or all of them) from the server, with infinite scroll.
//
// Layout rules (unchanged from the first version): exactly four quadrant slots; a tile's size comes from
// occupancy alone (100% for one, halves for two, quarters for three or four); anything dropped away from a
// quadrant floats. New in this version: everything that can be dragged can also be moved from a menu.
import { state, on, emit, byDomain, totalJobs } from './state.js';
import { filters, isFiltering } from './filters.js';
import { paneData, forgetPaneData, allPaneData } from './jobs.js';
import { bodyHTML, footerHTML, rowHTML } from './table.js';
import { openPopover, closePopover } from './popover.js';
import { $, $$, esc, clamp, debounce, fmtNum, svgIcon } from './util.js';

const QUADS = ['tl', 'tr', 'bl', 'br'];
const QUAD_NAMES = { tl: 'top left', tr: 'top right', bl: 'bottom left', br: 'bottom right' };
const MAX_VIEWS = 4;
const FOLD_H = 36;
const SNAP = 8, MIN_W = 420, MIN_H = 180;
const SNAP_BAND = 0.25;

const slots = { tl: 'MAIN', tr: null, bl: null, br: null };
const floats = [];
const foldedPanes = new Set();
let openTabs = [];

const dockEl = () => $('#dock');
const floatLayer = () => $('#floatLayer');
const workspace = () => $('#workspace');

const isRight = q => q === 'tr' || q === 'br';
const isBottom = q => q === 'bl' || q === 'br';
const slotOf = id => QUADS.find(q => slots[q] === id) || null;
const dockedIds = () => QUADS.map(q => slots[q]).filter(Boolean);
const isFloating = id => floats.some(f => f.domain === id);
const isPlaced = id => openTabs.includes(id) || dockedIds().includes(id) || isFloating(id);
const totalViews = () => openTabs.length + dockedIds().filter(id => id !== 'MAIN').length + floats.length;
const snap = v => Math.round(v / SNAP) * SNAP;
const targetOf = id => id === 'MAIN' ? state.activeTab : id;
const titleOf = id => id === 'MAIN' ? (state.activeTab === 'ALL' ? 'All companies' : state.activeTab) : id;

/* ------------------------------------------------------------------ geometry */
export function geometry(map, folded = foldedPanes) {
    const leftUsed = !!(map.tl || map.bl), rightUsed = !!(map.tr || map.br);
    const topUsed = !!(map.tl || map.tr), bottomUsed = !!(map.bl || map.br);
    const cols = (leftUsed ? 1 : 0) + (rightUsed ? 1 : 0);
    const rows = (topUsed ? 1 : 0) + (bottomUsed ? 1 : 0);
    const out = {};
    QUADS.forEach(q => {
        if (!map[q]) return;
        const width = cols === 2 ? '50%' : '100%';
        const left = (cols === 2 && isRight(q)) ? '50%' : '0%';
        const col = isRight(q) ? ['tr', 'br'] : ['tl', 'bl'];
        const bothInColumn = map[col[0]] && map[col[1]];
        let top, height;
        if (rows === 2 && bothInColumn) {
            const topFolded = folded.has(map[col[0]]), botFolded = folded.has(map[col[1]]);
            if (topFolded && !botFolded) {
                top = isBottom(q) ? `${FOLD_H}px` : '0px';
                height = isBottom(q) ? `calc(100% - ${FOLD_H}px)` : `${FOLD_H}px`;
            } else if (botFolded && !topFolded) {
                top = isBottom(q) ? `calc(100% - ${FOLD_H}px)` : '0px';
                height = isBottom(q) ? `${FOLD_H}px` : `calc(100% - ${FOLD_H}px)`;
            } else {
                top = isBottom(q) ? '50%' : '0%'; height = '50%';
            }
        } else if (rows === 2) {
            top = isBottom(q) ? '50%' : '0%';
            height = folded.has(map[q]) ? `${FOLD_H}px` : '50%';
        } else {
            top = '0%';
            height = folded.has(map[q]) ? `${FOLD_H}px` : '100%';
        }
        out[q] = { left, top, width, height };
    });
    return out;
}

/* ------------------------------------------------------------------ pane markup */
function headStat(id) {
    const key = targetOf(id);
    const data = paneData(key);
    const what = { current: 'postings', missing: 'gone', closed: 'closed' }[filters.status];
    const count = data.loadedOnce ? fmtNum(data.total) : '…';
    return `${count} ${data.loadedOnce && data.total === 1 && filters.status === 'current' ? 'posting' : what}${isFiltering() ? ' match' : ''}`;
}

function headExtra(id) {
    const c = byDomain(targetOf(id));
    if (c) return `${c.source || '—'} · ${c.new_count || 0} new`;
    return targetOf(id) === 'ALL' && state.companies.length ? `${state.companies.length} in the queue` : '';
}

function paneHTML(id) {
    const isMain = id === 'MAIN';
    const key = targetOf(id);
    const title = titleOf(id);
    const runId = key !== 'ALL' ? state.runView[key] : null;
    return `<div class="pane-head" data-pane-head="${esc(id)}" role="group" aria-label="${esc(title)}">
                <button type="button" class="icon-btn" data-fold="${esc(id)}" aria-label="${foldedPanes.has(id) ? 'Unfold' : 'Fold'} ${esc(title)}" aria-expanded="${!foldedPanes.has(id)}" title="Fold into the title bar">${foldedPanes.has(id) ? '▸' : '▾'}</button>
                <span class="pane-title truncate">${esc(title)}</span>
                <span class="pane-stat" data-stat="${esc(id)}">${esc(headStat(id))}</span>
                <span class="pane-stat truncate" data-extra="${esc(id)}">${esc(headExtra(id))}</span>
                <span class="pane-spacer"></span>
                <button type="button" class="icon-btn" data-menu="${esc(id)}" aria-label="Move ${esc(title)}" aria-haspopup="dialog" aria-expanded="false" title="Move or float this pane">${svgIcon('more')}</button>
                ${isMain ? '' : `<button type="button" class="icon-btn danger" data-return="${esc(id)}" aria-label="Return ${esc(title)} to the tab strip" title="Return to the tab strip">${svgIcon('x')}</button>`}
            </div>
            ${runId ? `<div class="run-banner">Viewing scan #${esc(runId)} <button type="button" class="btn" data-latest="${esc(key)}">Back to latest</button></div>` : ''}
            <div class="pane-body" data-pane-body="${esc(id)}" role="region" aria-label="Postings: ${esc(title)}" tabindex="0"></div>`;
}

const bodyFor = id => $$('[data-pane-body]').find(el => el.dataset.paneBody === id);

/* Fill a pane body from its data, keeping the scroll position, and (re)arm infinite scroll. */
const observers = new WeakMap();
function paintBody(body) {
    const pane = paneData(targetOf(body.dataset.paneBody));
    const top = pane.resetScroll ? 0 : body.scrollTop;
    pane.resetScroll = false;
    body.innerHTML = bodyHTML(pane);
    body.scrollTop = top;
    body.setAttribute('aria-busy', String(pane.loading));
    armSentinel(body, pane);
}

function armSentinel(body, pane) {
    observers.get(body)?.disconnect();
    const sentinel = body.querySelector('[data-sentinel]');
    if (!sentinel || !pane.hasMore) return;
    const observer = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting)) pane.loadMore();
    }, { root: body, rootMargin: '0px 0px 200px 0px' });
    observer.observe(sentinel);
    observers.set(body, observer);
}

function hydrate(root) {
    $$('[data-pane-body]', root).forEach(body => {
        const pane = paneData(targetOf(body.dataset.paneBody));
        paintBody(body);
        if (!pane.loadedOnce && !pane.loading) pane.refresh();
    });
    observePanes(root);
}

function updateHeads() {
    $$('[data-stat]').forEach(el => { el.textContent = headStat(el.dataset.stat); });
    $$('[data-extra]').forEach(el => { el.textContent = headExtra(el.dataset.extra); });
}

/* ------------------------------------------------------------------ dock + floats */
const ro = new ResizeObserver(entries => entries.forEach(e => {
    const w = e.contentRect.width;
    e.target.classList.toggle('narrow-1', w < 1000 && w >= 860);       // drop Department
    e.target.classList.toggle('narrow-2', w < 860 && w >= 700);        // ...and Type, Company
    e.target.classList.toggle('narrow-3', w < 700);                    // ...and Work
}));
function observePanes(root = document) { $$('.pane, .float', root).forEach(p => ro.observe(p)); }

export function renderDock() {
    const geo = geometry(slots);
    dockEl().innerHTML = '';
    QUADS.forEach(q => {
        const id = slots[q];
        if (!id) return;
        const tile = document.createElement('div');
        tile.className = 'tile';
        tile.dataset.quad = q;
        tile.dataset.tile = id;
        Object.assign(tile.style, geo[q]);
        const pane = document.createElement('div');
        pane.className = 'pane';
        pane.dataset.pane = id;
        pane.innerHTML = paneHTML(id);
        tile.appendChild(pane);
        dockEl().appendChild(tile);
    });
    hydrate(dockEl());
}

export function renderFloats() {
    floatLayer().innerHTML = '';
    floats.forEach(f => {
        const el = document.createElement('div');
        el.className = 'float' + (foldedPanes.has(f.domain) ? ' folded' : '');
        el.dataset.float = f.domain;
        Object.assign(el.style, { left: `${f.x}px`, top: `${f.y}px`, width: `${f.w}px`, height: `${f.h}px` });
        el.innerHTML = paneHTML(f.domain)
            + '<div class="resize-e" data-resize="e"></div><div class="resize-s" data-resize="s"></div><div class="resize-h" data-resize="se"></div>';
        floatLayer().appendChild(el);
    });
    hydrate(floatLayer());
}

export function renderAll() { renderDock(); renderFloats(); renderTabs(); emit('layout'); }

function addFloat(domain, clientX, clientY) {
    const r = workspace().getBoundingClientRect();
    const w = Math.min(660, r.width - 40), h = Math.min(400, r.height - 40);
    floats.push({
        domain, w: snap(w), h: snap(h),
        x: snap(clamp(clientX - r.left - w / 2, 0, Math.max(0, r.width - w))),
        y: snap(clamp(clientY - r.top - 16, 0, Math.max(0, r.height - h))),
    });
}

/* ------------------------------------------------------------------ moving panes (drag and menu share this) */
function detach(id, origin) {
    if (origin === 'strip') openTabs = openTabs.filter(d => d !== id);
    if (origin === 'dock') { const q = slotOf(id); if (q) slots[q] = null; }
    if (origin === 'float') { const i = floats.findIndex(f => f.domain === id); if (i > -1) floats.splice(i, 1); }
}

function originOf(id) {
    if (slotOf(id)) return 'dock';
    if (isFloating(id)) return 'float';
    return 'strip';
}

function dropInto(id, quad, origin) {
    const previous = slotOf(id);
    const occupant = slots[quad];
    detach(id, origin);
    if (occupant && occupant !== id) {
        if (previous) slots[previous] = occupant;
        else if (occupant !== 'MAIN') {
            slots[quad] = null;
            if (!openTabs.includes(occupant)) openTabs.push(occupant);
        } else {
            slots[previous || 'tl'] = occupant;
        }
    }
    slots[quad] = id;
}

function returnToStrip(id) {
    const q = slotOf(id); if (q) slots[q] = null;
    const i = floats.findIndex(f => f.domain === id); if (i > -1) floats.splice(i, 1);
    if (!openTabs.includes(id) && totalViews() < MAX_VIEWS) openTabs.push(id);
    renderAll();
}

/** The Move menu: the same moves as dragging, for keyboard and switch users. */
function openMoveMenu(anchor, id) {
    const origin = originOf(id);
    const title = titleOf(id);
    const items = QUADS.map(q => ({ label: `Dock ${QUAD_NAMES[q]}`, run: () => { dropInto(id, q, origin); renderAll(); }, current: slots[q] === id }));
    if (id !== 'MAIN') {
        items.push({ label: 'Float', run: () => { detach(id, origin); addFloat(id, window.innerWidth / 2, window.innerHeight / 3); renderAll(); }, current: isFloating(id) });
        if (origin !== 'strip') items.push({ label: 'Return to tab strip', run: () => returnToStrip(id) });
    }
    openPopover({
        anchor, label: `Move ${title}`, width: 230,
        render(panel, close) {
            panel.innerHTML = `<div class="popover-title label">Move ${esc(title)}</div><div class="popover-body">
                ${items.map((item, i) => `<button type="button" class="option" data-i="${i}" ${item.current ? 'aria-current="true"' : ''}><span class="truncate">${esc(item.label)}</span>${item.current ? '<span class="count">here</span>' : ''}</button>`).join('')}</div>`;
            $$('[data-i]', panel).forEach(button => button.onclick = () => { close(); items[Number(button.dataset.i)].run(); });
            panel.addEventListener('keydown', e => {
                if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
                const list = $$('.option', panel), at = list.indexOf(document.activeElement);
                e.preventDefault();
                list[(at + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length].focus();
            });
            return panel.querySelector('.option');
        },
    });
}

/* ------------------------------------------------------------------ dragging */
let drag = null;

function beginDrag(id, e, origin) {
    drag = { id, origin, ghost: document.createElement('div'), quad: null };
    drag.ghost.className = 'drag-ghost';
    drag.ghost.textContent = titleOf(id);
    document.body.appendChild(drag.ghost);
    moveGhost(e);
    showGuides(true);
    window.addEventListener('pointermove', onDragMove);
    window.addEventListener('pointerup', onDragEnd, { once: true });
}
const moveGhost = e => { drag.ghost.style.left = `${e.clientX + 12}px`; drag.ghost.style.top = `${e.clientY + 12}px`; };

function showGuides(on) {
    const guides = $('#quadGuides');
    guides.classList.toggle('on', on);
    guides.innerHTML = on ? QUADS.map(q =>
        `<i style="left:${isRight(q) ? '50%' : '0%'};top:${isBottom(q) ? '50%' : '0%'};width:50%;height:50%"></i>`).join('') : '';
}

function computeQuad(x, y) {
    const r = workspace().getBoundingClientRect();
    if (x < r.left || x > r.right || y < r.top || y > r.bottom) return null;
    const px = (x - r.left) / r.width, py = (y - r.top) / r.height;
    const inBand = px < SNAP_BAND || px > 1 - SNAP_BAND || py < SNAP_BAND || py > 1 - SNAP_BAND;
    if (!inBand) return null;
    return (py > 0.5 ? 'b' : 't') + (px > 0.5 ? 'r' : 'l');
}

function previewRect(id, quad) {
    const map = { ...slots };
    QUADS.forEach(q => { if (map[q] === id) map[q] = null; });
    map[quad] = id;
    const geo = geometry(map)[quad];
    const r = workspace().getBoundingClientRect();
    const toPx = (v, total) => v.endsWith('%') ? parseFloat(v) / 100 * total : parseFloat(v);
    return {
        left: toPx(geo.left, r.width), top: toPx(geo.top, r.height),
        width: geo.width.startsWith('calc') ? r.width : toPx(geo.width, r.width),
        height: geo.height.startsWith('calc') ? r.height : toPx(geo.height, r.height),
    };
}

function onDragMove(e) {
    moveGhost(e);
    drag.quad = computeQuad(e.clientX, e.clientY);
    const hint = $('#dropHint');
    if (!drag.quad) { hint.style.display = 'none'; return; }
    const rect = previewRect(drag.id, drag.quad);
    hint.style.display = 'block';
    Object.assign(hint.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
}

function onDragEnd(e) {
    window.removeEventListener('pointermove', onDragMove);
    drag.ghost.remove();
    $('#dropHint').style.display = 'none';
    showGuides(false);
    const { id, origin } = drag;
    const quad = computeQuad(e.clientX, e.clientY);
    drag = null;
    if (quad) dropInto(id, quad, origin);
    else if (id !== 'MAIN') { detach(id, origin); addFloat(id, e.clientX, e.clientY); }
    renderAll();
}

/* floating window: move by head, resize by edges */
function initFloatPointer() {
    floatLayer().addEventListener('pointerdown', e => {
        const el = e.target.closest('.float');
        if (!el) return;
        const f = floats.find(x => x.domain === el.dataset.float);
        if (!f || e.target.closest('button')) return;
        const wr = workspace().getBoundingClientRect();
        const handle = e.target.closest('[data-resize]');
        if (handle) {
            e.preventDefault();
            const kind = handle.dataset.resize;
            const move = ev => {
                if (kind !== 's') f.w = snap(clamp(ev.clientX - wr.left - f.x, MIN_W, wr.width - f.x));
                if (kind !== 'e') f.h = snap(clamp(ev.clientY - wr.top - f.y, MIN_H, wr.height - f.y));
                el.style.width = `${f.w}px`; el.style.height = `${f.h}px`;
            };
            window.addEventListener('pointermove', move);
            window.addEventListener('pointerup', () => window.removeEventListener('pointermove', move), { once: true });
            return;
        }
        if (!e.target.closest('.pane-head')) return;
        e.preventDefault();
        let armed = false;
        const move = ev => {
            if (!armed && Math.hypot(ev.clientX - e.clientX, ev.clientY - e.clientY) < 5) return;
            armed = true;
            window.removeEventListener('pointermove', move);
            beginDrag(f.domain, ev, 'float');
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', () => window.removeEventListener('pointermove', move), { once: true });
    });
}

/* ------------------------------------------------------------------ tab strip */
export function renderTabs() {
    const strip = $('#tabStrip');
    const focused = document.activeElement?.closest?.('[data-tab]')?.dataset.tab;
    const tab = (id, label, count, closable) => {
        const active = state.activeTab === id;
        return `<div class="tab" data-active="${active}" role="presentation">
            <button type="button" class="tab-main" role="tab" data-tab="${esc(id)}" aria-selected="${active}" tabindex="${active ? 0 : -1}"
                ${id === 'ALL' ? '' : 'title="Drag to a quadrant to snap it, anywhere else to float it. Press M for a menu."'}>
                <span class="tab-label">${esc(label)}</span><span class="tab-count">${fmtNum(count)}</span></button>
            ${closable ? `<button type="button" class="icon-btn tab-close" data-close="${esc(id)}" aria-label="Close ${esc(id)}" tabindex="${active ? 0 : -1}">${svgIcon('x')}</button>` : ''}</div>`;
    };
    const all = paneData('ALL');
    let html = tab('ALL', 'All', all.loadedOnce ? all.total : totalJobs(), false);
    openTabs.forEach(d => { const c = byDomain(d); if (c) html += tab(d, d, c.jobs_count || 0, true); });
    const out = totalViews() - openTabs.length;
    if (out > 0) html += `<span class="tab-note">${out} popped out</span>`;
    strip.innerHTML = html;
    if (focused) strip.querySelector(`[data-tab="${CSS.escape(focused)}"]`)?.focus();
}

export function selectTab(id) {
    if (state.activeTab === id) return;
    state.activeTab = id;
    renderTabs();
    renderDock();
    refreshVisible({ live: true });                               // cached rows show at once; fresh ones follow
    emit('active-tab', id);
}

/** Bring a company into view without discarding what is already loaded for it (used by the queue list). */
export function showCompany(domain) {
    if (!isPlaced(domain)) {
        if (totalViews() >= MAX_VIEWS) openTabs.shift();
        openTabs.push(domain);
    }
    if (state.activeTab === domain) { renderTabs(); return; }
    state.activeTab = domain;
    renderTabs();
    renderDock();
    refreshVisible({ live: true });
    emit('active-tab', domain);
}

function initTabs() {
    const strip = $('#tabStrip');
    strip.addEventListener('pointerdown', e => {
        const closeBtn = e.target.closest('[data-close]');
        if (closeBtn) return;                                    // handled on click
        const tab = e.target.closest('[data-tab]');
        if (!tab || e.button !== 0) return;
        const id = tab.dataset.tab;
        const startX = e.clientX, startY = e.clientY;
        let started = false;
        const pre = ev => {
            if (started || Math.hypot(ev.clientX - startX, ev.clientY - startY) < 6) return;
            if (id === 'ALL') return;
            started = true;
            window.removeEventListener('pointermove', pre);
            beginDrag(id, ev, 'strip');
        };
        const up = () => {
            window.removeEventListener('pointermove', pre);
            if (!started) selectTab(id);
        };
        window.addEventListener('pointermove', pre);
        window.addEventListener('pointerup', up, { once: true });
    });

    strip.addEventListener('click', e => {
        const closeBtn = e.target.closest('[data-close]');
        if (closeBtn) {
            const id = closeBtn.dataset.close;
            openTabs = openTabs.filter(d => d !== id);
            if (state.activeTab === id) { state.activeTab = 'ALL'; emit('active-tab', 'ALL'); }
            renderTabs(); renderDock();
            return;
        }
        const tab = e.target.closest('[data-tab]');
        if (tab && e.detail === 0) selectTab(tab.dataset.tab);   // keyboard activation; pointer selection is handled above
    });

    strip.addEventListener('keydown', e => {
        const tabs = $$('[role="tab"]', strip);
        const at = tabs.indexOf(document.activeElement);
        if (at < 0) return;
        const move = { ArrowRight: 1, ArrowLeft: -1, Home: -at, End: tabs.length - 1 - at }[e.key];
        if (move !== undefined) {
            e.preventDefault();
            const next = tabs[clamp(at + move, 0, tabs.length - 1)];
            next.focus();
            selectTab(next.dataset.tab);
        } else if ((e.key === 'm' || e.key === 'M' || e.key === 'ContextMenu') && tabs[at].dataset.tab !== 'ALL') {
            e.preventDefault();
            openMoveMenu(tabs[at], tabs[at].dataset.tab);
        } else if ((e.key === 'Delete' || e.key === 'Backspace') && tabs[at].dataset.tab !== 'ALL') {
            e.preventDefault();
            strip.querySelector(`[data-close="${CSS.escape(tabs[at].dataset.tab)}"]`)?.click();
        }
    });
}

/* ------------------------------------------------------------------ opening companies */
export function openCompany(domain, { runId = null } = {}) {
    if (runId) state.runView[domain] = runId;
    if (!isPlaced(domain)) {
        if (totalViews() >= MAX_VIEWS) openTabs.shift();
        openTabs.push(domain);
    }
    forgetPaneData(domain);                                       // start clean: a different scan may be showing
    const wasActive = state.activeTab === domain;
    state.activeTab = domain;
    renderTabs();
    renderAll();
    if (!wasActive) emit('active-tab', domain);
}

/* ------------------------------------------------------------------ data refresh */
const visibleKeys = () => [...new Set([
    ...dockedIds().map(targetOf), ...floats.map(f => f.domain),
])];

export function refreshVisible({ live = false } = {}) {
    visibleKeys().forEach(key => paneData(key).refresh({ live }));
}

const refreshLive = debounce(() => refreshVisible({ live: true }), 500);

/** Called for every server state message. */
export function onServerState(s, previous) {
    let structural = false;
    for (const ev of (s.events || [])) {
        if (ev.kind === 'reset') {
            delete state.runView[ev.domain];                      // a fresh scan: back to looking at the latest
            forgetPaneData(ev.domain);
            structural = true;
        } else if (ev.kind === 'jobs') {
            refreshLive();
        }
    }
    state.companies.forEach(c => {                                // give every new company a tab, if there is room
        if (!isPlaced(c.domain) && totalViews() < MAX_VIEWS) { openTabs.push(c.domain); structural = true; }
    });
    const key = c => `${c.domain}:${c.state}:${c.source}`;
    if (previous.map(key).join('|') !== state.companies.map(key).join('|')) structural = true;
    renderTabs();
    if (structural) { renderDock(); renderFloats(); }
    else updateHeads();
}

/** Infinite scroll also listens to plain scrolling, so it works even where IntersectionObserver is unreliable. */
function initScrollLoading() {
    const check = debounce(body => {
        const pane = paneData(targetOf(body.dataset.paneBody));
        if (pane.hasMore && body.scrollTop + body.clientHeight >= body.scrollHeight - 300) pane.loadMore();
    }, 60);
    document.addEventListener('scroll', event => {
        const body = event.target.closest?.('[data-pane-body]');
        if (body) check(body);
    }, true);
}

function initDelegates() {
    document.addEventListener('pointerdown', e => {
        const foldBtn = e.target.closest('[data-fold]');
        if (foldBtn) {
            const id = foldBtn.dataset.fold;
            foldedPanes.has(id) ? foldedPanes.delete(id) : foldedPanes.add(id);
            renderDock(); renderFloats();
            e.stopPropagation();
            return;
        }
        if (e.target.closest('[data-return],[data-menu]')) return;
        const head = e.target.closest('.pane-head[data-pane-head]');
        if (head && head.closest('.tile') && e.button === 0) {
            const id = head.dataset.paneHead;
            const startX = e.clientX, startY = e.clientY;
            let started = false;
            const pre = ev => {
                if (started || Math.hypot(ev.clientX - startX, ev.clientY - startY) < 5) return;
                started = true;
                window.removeEventListener('pointermove', pre);
                beginDrag(id, ev, 'dock');
            };
            window.addEventListener('pointermove', pre);
            window.addEventListener('pointerup', () => window.removeEventListener('pointermove', pre), { once: true });
        }
    });

    document.addEventListener('click', e => {
        const foldKey = e.target.closest('[data-fold]');
        if (foldKey && e.detail === 0) {                            // keyboard: pointer folding happens on pointerdown
            const id = foldKey.dataset.fold;
            foldedPanes.has(id) ? foldedPanes.delete(id) : foldedPanes.add(id);
            renderDock(); renderFloats();
            return;
        }
        const ret = e.target.closest('[data-return]');
        if (ret) { returnToStrip(ret.dataset.return); return; }
        const menu = e.target.closest('[data-menu]');
        if (menu) { openMoveMenu(menu, menu.dataset.menu); return; }
        const sort = e.target.closest('[data-sort]');
        if (sort) { paneData(sort.dataset.pane).cycleSort(sort.dataset.sort); return; }
        const more = e.target.closest('[data-load-more]');
        if (more) { paneData(more.dataset.loadMore).loadMore(); return; }
        const retry = e.target.closest('[data-retry]');
        if (retry) { paneData(retry.dataset.retry).refresh(); return; }
        if (e.target.closest('[data-clear-filters]')) { $('#clearFilters').click(); return; }
        const latest = e.target.closest('[data-latest]');
        if (latest) { delete state.runView[latest.dataset.latest]; forgetPaneData(latest.dataset.latest); renderAll(); emit('active-tab', state.activeTab); }
    });
}

/* ------------------------------------------------------------------ data events -> DOM */
function initDataEvents() {
    on('pane-data', key => {
        $$('[data-pane-body]').forEach(body => { if (targetOf(body.dataset.paneBody) === key) paintBody(body); });
        updateHeads();
        if (key === 'ALL') renderTabs();                          // the All tab's count is the server's total
    });
    on('pane-more', ({ key, state: phase, rows }) => {
        $$('[data-pane-body]').forEach(body => {
            if (targetOf(body.dataset.paneBody) !== key) return;
            const pane = paneData(key);
            const sentinel = body.querySelector('[data-sentinel]');
            if (!sentinel) return;
            if (phase === 'appended') {                           // add the new rows in place: the scroll position never moves
                body.querySelector('tbody').insertAdjacentHTML('beforeend', rows.map(job => rowHTML(job, key)).join(''));
                return;
            }
            sentinel.outerHTML = footerHTML(pane);                // 'loading' and 'idle' both repaint the footer from the data
            armSentinel(body, pane);
        });
        updateHeads();
    });
    on('filters', () => { refreshVisible(); });
    on('layout', () => {});
}

export function initWorkspace() {
    initTabs();
    initFloatPointer();
    initDelegates();
    initScrollLoading();
    initDataEvents();
    renderAll();
}

/* exposed for the History view and tests */
export const _internals = { slots, floats, openTabs: () => openTabs, geometry };
