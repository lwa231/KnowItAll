// Views: which one shows, which rail button is current, and what each one loads when it opens.
import { state, emit } from './state.js';
import { loadHistory } from './views/history.js';
import { loadOutput } from './views/output.js';
import { loadSettingsView } from './views/settings.js';
import { startSystemPolling, stopSystemPolling } from './views/system.js';
import { $$ } from './util.js';

/** Switch view. `section` (Settings only) names a panel to scroll to; the breadcrumbs then show it. */
export function showView(name, { section = null } = {}) {
    state.activeView = name;
    state.settingsSection = name === 'settings' ? section : null;
    $$('.rail-item[data-view]').forEach(item => {
        if (item.dataset.view === name) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
    });
    $$('.view').forEach(view => view.classList.toggle('active', view.dataset.view === name));
    stopSystemPolling();
    if (name === 'history') loadHistory();
    if (name === 'output') loadOutput();
    if (name === 'settings') loadSettingsView();
    if (name === 'system') startSystemPolling();
    if (name === 'settings') scrollToSection(section);
    emit('view', name);
}

/** Bring a Settings panel to the top of the view (only its own scroll box moves, never the app frame); no section means the top. */
function scrollToSection(section) {
    const box = document.querySelector('.view[data-view="settings"] .view-scroll');
    const target = section && document.getElementById(`settings-${section}`);
    if (box) box.scrollTop = target ? box.scrollTop + target.getBoundingClientRect().top - box.getBoundingClientRect().top - 24 : 0;
}

export function initRail() {
    $$('.rail-item[data-view]').forEach(item => item.addEventListener('click', () => showView(item.dataset.view)));
}
