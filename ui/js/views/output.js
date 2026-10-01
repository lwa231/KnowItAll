// Output: the files written to the exports folder (this session's first), "Export this session", and backups.
import * as api from '../api.js';
import { state } from '../state.js';
import { filters, isFiltering } from '../filters.js';
import { toast } from '../notify.js';
import { mountBackups } from './backups.js';
import { $, esc, relTime, fmtNum } from '../util.js';

const row = f => `<tr><td class="bright">${esc(f.name)}</td><td class="muted">${f.rows == null ? '—' : fmtNum(f.rows)}</td>
    <td class="muted">${esc(f.size)}</td><td class="right dim">${relTime(new Date(f.written * 1000))}</td></tr>`;

export async function loadOutput() {
    const body = $('#outputBody');
    const matching = (state.settings?.export_scope || 'matching') === 'matching';
    $('#exportScopeHint').textContent = matching && isFiltering() ? 'Filters are on, so the export holds matching postings only.' : '';
    try {
        const { files } = await api.get('/api/output');
        if (!files.length) { body.innerHTML = '<tr><td colspan="4" class="dim">Nothing written yet.</td></tr>'; return; }
        const now = files.filter(f => f.this_session), earlier = files.filter(f => !f.this_session);
        body.innerHTML = now.map(row).join('')
            + (earlier.length ? `<tr class="group-row"><td colspan="4" class="label">Earlier files</td></tr>${earlier.map(row).join('')}` : '');
    } catch { body.innerHTML = '<tr><td colspan="4" class="bad">Could not list the exports folder.</td></tr>'; }
}

export async function openFolder() {
    try { const { opened } = await api.post('/api/open-folder'); if (!opened) toast('Could not open the folder', { kind: 'bad' }); }
    catch { toast('Could not open the folder', { kind: 'bad' }); }
}

export function initOutput() {
    $('#openFolderBtn').addEventListener('click', openFolder);
    mountBackups($('#outputBackups'));
    $('#exportAllBtn').addEventListener('click', async () => {
        try {
            const result = await api.post('/api/export', { filters: { ...filters } });
            toast(result.written ? `Exported ${fmtNum(result.rows)} ${result.rows === 1 ? 'posting' : 'postings'}${result.filtered ? ' (matching your filters)' : ''} to ${result.written}` : 'Nothing to export yet this session');
            loadOutput();
        } catch { toast('Export failed', { kind: 'bad' }); }
    });
}
