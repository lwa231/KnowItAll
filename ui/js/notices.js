// The notice bar above the workspace: persistent, non-blocking warnings (Chrome missing, lost connection).
import { state } from './state.js';
import { $, esc, svgIcon } from './util.js';

const dismissed = new Set();               // for this session only: the warning returns next launch if it still applies

export function renderNotices() {
    const items = state.notices.filter(n => !dismissed.has(n.code)).map(n => ({ ...n, action: n.code === 'no_chrome' ? 'system' : null }));
    if (state.connection === 'reconnecting' || state.connection === 'polling') {
        items.push({ code: 'offline', level: 'error', text: 'Lost contact with the app. Reconnecting…', action: null, quiet: true });
    }
    const bar = $('#noticeBar');
    const html = items.map(n => `<div class="notice ${n.level === 'error' ? 'error' : ''}" data-notice="${esc(n.code)}">
        ${svgIcon('alert')}<span class="notice-text">${esc(n.text)}</span>
        ${n.action ? `<button type="button" class="btn" data-open-view="${n.action}">Open System</button>` : ''}
        ${n.quiet ? '' : `<button type="button" class="icon-btn" data-dismiss="${esc(n.code)}" aria-label="Dismiss this notice">${svgIcon('x')}</button>`}</div>`).join('');
    if (bar.dataset.html !== html) { bar.innerHTML = html; bar.dataset.html = html; }
    bar.setAttribute('role', items.some(n => n.level === 'error') ? 'alert' : 'status');
}

export function initNotices() {
    $('#noticeBar').addEventListener('click', event => {
        const dismiss = event.target.closest('[data-dismiss]');
        if (dismiss) { dismissed.add(dismiss.dataset.dismiss); renderNotices(); return; }
        const open = event.target.closest('[data-open-view]');
        if (open) document.querySelector(`.nav-item[data-view="${open.dataset.openView}"]`).click();
    });
}
