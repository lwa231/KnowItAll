// Output: the files written to the exports folder.
import * as api from '../api.js';
import { toast } from '../notify.js';
import { $, esc, relTime, fmtNum } from '../util.js';

export async function loadOutput() {
    const body = $('#outputBody');
    try {
        const { files } = await api.get('/api/output');
        body.innerHTML = files.length ? files.map(f => `<tr>
            <td class="bright">${esc(f.name)}</td><td class="muted">${f.rows == null ? '—' : fmtNum(f.rows)}</td>
            <td class="muted">${esc(f.size)}</td><td class="right dim">${relTime(new Date(f.written * 1000))}</td></tr>`).join('')
            : '<tr><td colspan="4" class="dim">Nothing written yet.</td></tr>';
    } catch { body.innerHTML = '<tr><td colspan="4" class="bad">Could not list the exports folder.</td></tr>'; }
}

export async function openFolder() {
    try { const { opened } = await api.post('/api/open-folder'); if (!opened) toast('Could not open the folder', { kind: 'bad' }); }
    catch { toast('Could not open the folder', { kind: 'bad' }); }
}

export function initOutput() {
    $('#openFolderBtn').addEventListener('click', openFolder);
    $('#exportAllBtn').addEventListener('click', async () => {
        try {
            const result = await api.post('/api/export');
            toast(result.written ? `Exported ${result.rows} postings to ${result.written}` : 'Nothing to export yet');
            loadOutput();
        } catch { toast('Export failed', { kind: 'bad' }); }
    });
}
