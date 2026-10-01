// The backups list with "Back up now" and "Open backups folder". One component, used in Settings (Data & storage) and on
// the Output page, so the two always agree. Restoring is not offered yet.
import * as api from '../api.js';
import { toast } from '../notify.js';
import { esc, fmtBytes, relTime } from '../util.js';

const mounted = new Set();

export async function loadBackups() { await Promise.all([...mounted].map(view => view.load())); }

export function mountBackups(container) {
    container.innerHTML = `<div class="toolbar"><button class="btn" type="button" data-backup-now>Back up now</button>
        <button class="btn" type="button" data-backup-folder>Open backups folder</button></div>
        <table class="plain-table"><caption class="sr-only">Backups of your history</caption>
        <thead><tr><th scope="col" style="width:40%">Made</th><th scope="col" style="width:30%">Kind</th><th scope="col" class="right" style="width:30%">Size</th></tr></thead>
        <tbody data-backup-rows></tbody></table>`;
    const rows = container.querySelector('[data-backup-rows]');
    const view = {
        async load() {
            try {
                const { backups } = await api.get('/api/backups');
                rows.innerHTML = backups.length ? backups.map(b => `<tr><td class="bright" title="${esc(b.name)}">${esc(new Date(b.created * 1000).toLocaleString())} <span class="dim">(${esc(relTime(new Date(b.created * 1000)))})</span></td>
                    <td class="muted">${b.kind === 'auto' ? 'Automatic' : 'Manual'}</td><td class="right muted">${esc(fmtBytes(b.bytes))}</td></tr>`).join('')
                    : '<tr><td colspan="3" class="dim">No backups yet. One is made at launch once there is something to keep.</td></tr>';
            } catch { rows.innerHTML = '<tr><td colspan="3" class="bad">Could not list the backups.</td></tr>'; }
        },
    };
    container.querySelector('[data-backup-now]').addEventListener('click', async event => {
        const button = event.currentTarget;
        button.disabled = true;
        try { await api.post('/api/backups'); toast('Backup saved'); await loadBackups(); }
        catch (error) { toast(error.message || 'Could not make a backup', { kind: 'bad' }); }
        finally { button.disabled = false; }
    });
    container.querySelector('[data-backup-folder]').addEventListener('click', async () => {
        try { const { opened } = await api.post('/api/backups/open-folder'); if (!opened) toast('Could not open the folder', { kind: 'bad' }); }
        catch { toast('Could not open the folder', { kind: 'bad' }); }
    });
    mounted.add(view);
    view.load();
    return view;
}
