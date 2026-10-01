// Settings: bound two-way to the server-side settings.json (see settings.js), plus Data & storage.
import * as api from '../api.js';
import { state } from '../state.js';
import { bindThemeSwitch, bindSwitch, bindSegmented } from '../settings.js';
import { bindSteppedSlider } from '../slider.js';
import { openFolder } from './output.js';
import { mountBackups, loadBackups } from './backups.js';
import { toast } from '../notify.js';
import { $, esc, fmtBytes, fmtNum } from '../util.js';

export const TIME_STOPS = [1, 3, 5];
export const JOB_STOPS = [100, 500, 1000, 2000, 5000, 10000, 50000, 100000];
export const DEPTH_STOPS = [0, 10, 25, 50, 100, 250, 500];

const stat = (label, value) => `<div class="stat"><span class="label">${esc(label)}</span><span class="stat-value">${esc(value)}</span></div>`;

/** The page cache and database sizes, and whether Clear cache / Compact may be used right now. */
export async function loadStorage() {
    try {
        const info = await api.get('/api/system');
        const u = info.usage;
        $('#dsStats').innerHTML = stat('Saved pages (cache)', fmtBytes(u.cache_bytes)) + stat('Database', fmtBytes(u.database_bytes))
            + stat('Logs', fmtBytes(u.logs_bytes)) + stat('Exports', fmtBytes(u.exports_bytes));
        paintStorageButtons(info.running);
    } catch { $('#dsStats').textContent = 'Could not read the storage numbers.'; }
}

export function paintStorageButtons(running = state.running) {
    $('#clearCacheBtn').disabled = running;
    $('#compactBtn').disabled = running;
    $('#clearCacheBtn').title = running ? 'A scan is running: a scan could be reading those files' : '';
    $('#storageHint').textContent = running
        ? 'A scan is running, so the cache cannot be cleared and the database cannot be compacted right now.'
        : 'Old saved pages, old scans and long-closed postings are also removed automatically at start-up.';
}

export async function loadSettingsView() {
    try { const info = await api.get('/api/system'); $('#exportsPath').textContent = info.paths.exports; }
    catch { $('#exportsPath').textContent = 'unknown'; }
    loadStorage();
    loadBackups();
}

export function initSettingsView() {
    bindThemeSwitch($('#themeSwitch'));
    bindSteppedSlider($('#setLimit'), 'time_limit_min', TIME_STOPS, n => `${n} min`, { valueText: n => `${n} ${n === 1 ? 'minute' : 'minutes'}`, marks: [[0, '1 min'], [1, '3 min'], [2, '5 min']] });
    bindSteppedSlider($('#setMaxJobs'), 'max_jobs', JOB_STOPS, fmtNum, { valueText: n => `${fmtNum(n)} postings`, marks: [[0, '100'], [3, '2k'], [5, '10k'], [7, '100k']] });
    bindSteppedSlider($('#setDepth'), 'max_enrich', DEPTH_STOPS, n => `${fmtNum(n)} ${n === 1 ? 'page' : 'pages'}`, { marks: [[0, '0'], [3, '50'], [6, '500']] });
    bindSegmented($('#modeSeg'), 'parallel_mode');
    bindSegmented($('#atOnceSeg2'), 'concurrency', Number);
    bindSegmented($('#reuseSeg'), 'cache_reuse');
    bindSegmented($('#scopeSeg'), 'export_scope');
    bindSwitch($('#setAutosave'), 'autosave');
    bindSwitch($('#setAutoBackup'), 'auto_backup');
    $('#openFolderBtn2').addEventListener('click', openFolder);
    mountBackups($('#settingsBackups'));
    $('#clearCacheBtn').addEventListener('click', async () => {
        try {
            const r = await api.post('/api/maintenance/clear-cache');
            toast(`Cleared ${fmtNum(r.files)} saved ${r.files === 1 ? 'page' : 'pages'} (${fmtBytes(r.bytes)}). The next scan of each company downloads everything fresh.`);
            loadStorage();
        } catch (error) { toast(error.message || 'Could not clear the cache', { kind: 'bad' }); }
    });
    $('#compactBtn').addEventListener('click', async () => {
        try { const r = await api.post('/api/maintenance/compact'); toast(`Database compacted: ${fmtBytes(r.before)} → ${fmtBytes(r.after)}`); loadStorage(); }
        catch (error) { toast(error.message || 'Could not compact the database', { kind: 'bad' }); }
    });
}
