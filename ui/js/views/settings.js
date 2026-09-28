// Settings: bound two-way to the server-side settings.json (see settings.js).
import * as api from '../api.js';
import { bindThemeSwitch, bindSwitch, bindNumber } from '../settings.js';
import { openFolder } from './output.js';
import { $ } from '../util.js';

export async function loadSettingsView() {
    try { const info = await api.get('/api/system'); $('#exportsPath').textContent = info.paths.exports; }
    catch { $('#exportsPath').textContent = 'unknown'; }
}

export function initSettingsView() {
    bindThemeSwitch($('#themeSwitch'));
    bindNumber($('#setMaxJobs'), 'max_jobs');
    bindNumber($('#setDepth'), 'max_enrich');
    bindNumber($('#setConcurrency'), 'concurrency');
    bindSwitch($('#setFresh'), 'fresh');
    bindSwitch($('#setAutosave'), 'autosave');
    $('#openFolderBtn2').addEventListener('click', openFolder);
}
