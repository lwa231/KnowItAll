// Boot: load settings, wire every module together, connect to the live stream.
import * as api from './api.js';
import { state, on } from './state.js';
import { loadSettings, syncBindings } from './settings.js';
import { logLine } from './notify.js';
import { initFilterBar, refreshFacets, refreshFacetsLive } from './filterbar.js';
import { initWorkspace, onServerState, refreshVisible } from './workspace.js';
import { initQueue, renderQueue } from './queue.js';
import { initRun, paintRunState, paintMetrics, announceRunChange } from './run.js';
import { initNotices, renderNotices } from './notices.js';
import { initHistory, loadHistory } from './views/history.js';
import { initOutput, loadOutput } from './views/output.js';
import { initSettingsView, loadSettingsView } from './views/settings.js';
import { initSystem, startSystemPolling, stopSystemPolling } from './views/system.js';
import { $, $$ } from './util.js';

function showView(name) {
    state.activeView = name;
    $$('.nav-item[data-view]').forEach(item => {
        if (item.dataset.view === name) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
    });
    $$('.view').forEach(view => view.classList.toggle('active', view.dataset.view === name));
    stopSystemPolling();
    if (name === 'history') loadHistory();
    if (name === 'output') loadOutput();
    if (name === 'settings') loadSettingsView();
    if (name === 'system') startSystemPolling();
}

function handleState(incoming) {
    const previous = state.companies;
    const wasRunning = state.running;
    state.cursor = incoming.cursor;
    state.running = incoming.running;
    state.companies = incoming.companies || [];
    state.notices = incoming.notices || [];
    let jobsChanged = false;
    for (const event of (incoming.events || [])) {
        if (event.kind === 'log') logLine(event.text);
        if (event.kind === 'jobs') jobsChanged = true;
    }
    if (state.running && !wasRunning) state.elapsed = 0;
    onServerState(incoming, previous);
    renderQueue(); paintRunState(); paintMetrics(); renderNotices();
    announceRunChange(wasRunning);
    if (jobsChanged) refreshFacetsLive();
    if (wasRunning && !state.running) {                                  // a scan just ended: settle every count
        refreshFacets();
        refreshVisible({ live: true });
        if (state.activeView === 'history') loadHistory();
        if (state.activeView === 'output') loadOutput();
    }
}

async function boot() {
    await loadSettings();
    on('settings', syncBindings);

    $$('.nav-item[data-view]').forEach(item => item.addEventListener('click', () => showView(item.dataset.view)));
    initRun(); initQueue(); initNotices(); initFilterBar(); initWorkspace();
    initHistory(showView); initOutput(); initSettingsView(); initSystem();
    renderQueue(); paintRunState(); paintMetrics(); renderNotices();

    document.addEventListener('keydown', event => {
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;
        if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) { event.preventDefault(); $('#searchInput').focus(); $('#searchInput').select(); }
    });

    api.connect({
        getCursor: () => state.cursor,
        onState: handleState,
        onStatus: status => { state.connection = status; paintRunState(); renderNotices(); if (status === 'live') refreshVisible({ live: true }); },
    });
}

boot().catch(error => { console.error(error); document.body.insertAdjacentHTML('afterbegin', `<pre style="padding:16px;color:#e06c75">KnowItAll could not start: ${String(error)}</pre>`); });
