// The breadcrumb trail in the top bar (HyperUI "Base with home icon"). Every crumb but the last is a button; the last
// is plain text marked aria-current="page". The trail is computed from the state, so it is always where you are.
import { state, on } from './state.js';
import { $, esc } from './util.js';

const VIEW_NAMES = { scraper: 'Scraper', history: 'History', output: 'Output', settings: 'Settings', system: 'System' };
export const SECTION_NAMES = { scanning: 'Scanning', appearance: 'Appearance', output: 'Output' };

/** The trail as [{label, act}] ("act" names what a click does; the last crumb has none). Pure: reads `state` only. */
export function trail() {
    const view = state.activeView;
    const home = { label: 'Home', act: 'home', home: true };
    if (view === 'settings') {
        const section = SECTION_NAMES[state.settingsSection];
        return section ? [home, { label: 'Settings', act: 'settings' }, { label: section }] : [home, { label: 'Settings' }];
    }
    if (view !== 'scraper') return [home, { label: VIEW_NAMES[view] || view }];
    const domain = state.activeTab;
    const runId = domain !== 'ALL' ? state.runView[domain] : null;
    if (domain === 'ALL') return [home, { label: 'Scraper' }];
    const first = state.openedFrom === 'history' && runId ? { label: 'History', act: 'history' } : { label: 'Scraper', act: 'scraper' };
    if (!runId) return [home, first, { label: domain }];
    return [home, first, { label: domain, act: `latest:${domain}` }, { label: `Scan #${runId}` }];
}

export function renderCrumbs() {
    const items = trail();
    const chevron = '<li class="crumb-sep" aria-hidden="true"><svg focusable="false"><use href="#h-chevron"/></svg></li>';
    $('#crumbList').innerHTML = items.map((item, i) => {
        if (i === items.length - 1) return `<li><span class="crumb-current" aria-current="page">${esc(item.label)}</span></li>`;
        const body = item.home ? '<svg aria-hidden="true" focusable="false"><use href="#h-home"/></svg>' : esc(item.label);
        return `<li><button class="crumb" type="button" data-act="${esc(item.act)}"${item.home ? ' aria-label="Home"' : ''}>${body}</button></li>`;
    }).join(chevron);
}

/** `go` holds what a crumb can do: showView, selectTab and showLatest (passed in so this file imports no view code). */
export function initBreadcrumbs({ showView, selectTab, showLatest }) {
    $('#crumbList').addEventListener('click', event => {
        const button = event.target.closest('[data-act]');
        if (!button) return;
        const act = button.dataset.act;
        if (act === 'home' || act === 'scraper') { showView('scraper'); selectTab('ALL'); }
        else if (act === 'history') showView('history');
        else if (act === 'settings') showView('settings');
        else if (act.startsWith('latest:')) showLatest(act.slice('latest:'.length));
    });
    on('view', () => { state.openedFrom = null; renderCrumbs(); });
    on('active-tab', () => { state.openedFrom = null; renderCrumbs(); });
    on('run-view', domain => { if (!state.runView[domain]) state.openedFrom = null; renderCrumbs(); });
    renderCrumbs();
}
