// Boot: load settings, wire every module together, connect to the live stream.
import * as api from './api.js';
import { state, on, emit } from './state.js';
import { loadSettings, syncBindings } from './settings.js';
import { logLine, announce } from './notify.js';
import { initFilterBar, refreshFacets, refreshFacetsLive } from './filterbar.js';
import { refreshCounts, refreshCountsLive } from './counts.js';
import { applySaved } from './filters.js';
import { initWorkspace, onServerState, refreshVisible, selectTab, showLatest } from './workspace.js';
import { initQueue, renderQueue } from './queue.js';
import { initQueuePanel } from './queuepanel.js';
import { initBreadcrumbs } from './breadcrumbs.js';
import { initRun, paintRunState, paintMetrics, announceRunChange } from './run.js';
import { initNotices, renderNotices } from './notices.js';
import { initHistory, loadHistory } from './views/history.js';
import { showView, initRail } from './nav.js';
import { initOutput, loadOutput } from './views/output.js';
import { initSettingsView } from './views/settings.js';
import { initSystem } from './views/system.js';
import { $ } from './util.js';

/** A company that has just finished with nothing to show says why, once, to screen readers (the log and panes show it too). */
function announceOutcomes(previous) {
    const before = new Map(previous.map(c => [c.domain, c.outcome]));
    const news = state.companies.filter(c => c.outcome && c.outcome !== 'found' && before.get(c.domain) !== c.outcome && c.outcome_detail);
    if (news.length) announce(news.map(c => `${c.domain}: ${c.outcome_detail}`).join(' '));
    news.forEach(c => logLine(`${c.domain}: ${c.outcome_detail}`));
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
    announceOutcomes(previous);
    if (jobsChanged) { refreshFacetsLive(); refreshCountsLive(); }
    if (wasRunning && !state.running) {                                  // a scan just ended: settle every count
        refreshFacets(); refreshCounts();
        refreshVisible({ live: true });
        if (state.activeView === 'history') loadHistory();
        if (state.activeView === 'output') loadOutput();
    }
}

async function boot() {
    await loadSettings();
    applySaved(state.settings.filters);                  // last time's chip filters, before the first feed is asked for
    on('settings', syncBindings);

    initRail();
    initRun(); initQueue(); initNotices(); initFilterBar(); initWorkspace();
    initHistory(showView); initOutput(); initSettingsView(); initSystem();
    initQueuePanel(showView); initBreadcrumbs({ showView, selectTab, showLatest });
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
