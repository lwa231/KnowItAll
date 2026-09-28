// System: whether the scraping browser works, which HTTP client is serving, disk use, and housekeeping.
import * as api from '../api.js';
import { state } from '../state.js';
import { setSetting } from '../settings.js';
import { toast } from '../notify.js';
import { $, $$, esc, fmtBytes, fmtNum } from '../util.js';

const stat = (label, value) => `<div class="stat"><span class="label">${esc(label)}</span><span class="stat-value">${esc(value)}</span></div>`;
let timer = null;

export async function loadSystem() {
    let info;
    try { info = await api.get('/api/system'); }
    catch { $('#browserStatus').innerHTML = '<span class="status-dot bad"></span>Could not read the system status.'; return; }

    const b = info.browser;
    const tone = !b.enabled ? 'warn' : b.available ? 'ok' : 'bad';
    const text = !b.enabled ? 'Browser scraping is off' : b.available ? `Chrome works (${b.active} open now)` : 'Chrome could not be started';
    $('#browserStatus').innerHTML = `<span class="status-dot ${tone}" aria-hidden="true"></span><span>${esc(text)}</span>`;
    $('#browserHint').textContent = b.available
        ? 'Used only for sites that need JavaScript; closes itself after two idle minutes.'
        : `${(b.reason || '').replace(/\.?$/, '.')} Pages that need JavaScript are skipped; everything else is unaffected.`.trim();
    $$('#workersSeg button').forEach(button => {
        const on = Number(button.dataset.workers) === (b.enabled ? b.workers : state.settings.browser_workers);
        button.setAttribute('aria-checked', String(on));
        button.tabIndex = on ? 0 : -1;
        button.disabled = !b.enabled;
    });

    const r = info.requests;
    $('#requestStats').innerHTML = stat('Browser-like client', fmtNum(r.botasaurus)) + stat('Plain requests (fallback)', fmtNum(r.requests))
        + stat('Timeouts', fmtNum(r.timeouts)) + stat('Still running after timeout', fmtNum(r.abandoned));

    const u = info.usage;
    $('#storageStats').innerHTML = stat('Page cache', fmtBytes(u.cache_bytes)) + stat('Database', fmtBytes(u.database_bytes))
        + stat('Logs', fmtBytes(u.logs_bytes)) + stat('Exports', fmtBytes(u.exports_bytes));
    $('#compactBtn').disabled = info.running;
    $('#storageHint').textContent = info.running
        ? 'A scan is running, so the database cannot be compacted right now.'
        : 'Old cache files, old scans and long-closed postings are removed automatically at start-up.';

    const p = info.paths, v = info.versions;
    $('#locations').innerHTML = [['Data', p.data], ['Exports', p.exports], ['Database', p.database], ['Logs', p.logs]]
        .map(([name, path]) => `<div class="setting"><div class="setting-text"><div class="setting-name">${esc(name)}</div><div class="setting-help path">${esc(path)}</div></div></div>`).join('')
        + `<p class="hint">Scraping engine: botasaurus ${esc(v.botasaurus || 'not found')}${v.botasaurus === v.tested_with ? ' (the version this app is tested with)' : ` (tested with ${esc(v.tested_with)})`}</p>`;
}

export function startSystemPolling() { stopSystemPolling(); loadSystem(); timer = setInterval(loadSystem, 4000); }
export function stopSystemPolling() { clearInterval(timer); timer = null; }

export function initSystem() {
    $('#workersSeg').addEventListener('click', event => {
        const button = event.target.closest('[data-workers]');
        if (!button || button.disabled) return;
        setSetting('browser_workers', Number(button.dataset.workers));
        setTimeout(loadSystem, 500);
    });
    $('#workersSeg').addEventListener('keydown', event => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const current = state.settings.browser_workers;
        const next = Math.max(1, Math.min(3, current + step));
        setSetting('browser_workers', next);
        setTimeout(() => { loadSystem().then(() => $(`#workersSeg [data-workers="${next}"]`)?.focus()); }, 500);
    });
    $('#clearCacheBtn').addEventListener('click', async () => {
        try { const r = await api.post('/api/maintenance/clear-cache'); toast(`Cache cleared: ${r.files} files, ${fmtBytes(r.bytes)}`); loadSystem(); }
        catch { toast('Could not clear the cache', { kind: 'bad' }); }
    });
    $('#compactBtn').addEventListener('click', async () => {
        try { const r = await api.post('/api/maintenance/compact'); toast(`Database compacted: ${fmtBytes(r.before)} → ${fmtBytes(r.after)}`); loadSystem(); }
        catch (error) { toast(error.message || 'Could not compact the database', { kind: 'bad' }); }
    });
}
