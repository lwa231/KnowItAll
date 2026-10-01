// The one shared state object, and a tiny publish/subscribe so modules never import each other in circles.

export const state = {
    companies: [],           // from the server: {domain, company, state, jobs_count, new_count, missing_count, closed_count, ...}
    notices: [],             // persistent warnings from the server (e.g. Chrome missing)
    running: false,
    cursor: 0,               // last event id received
    elapsed: 0,              // seconds since the current run started
    connection: 'connecting',
    settings: null,          // loaded from /api/settings
    runView: {},             // domain -> run id being looked at instead of the latest scan
    activeView: 'scraper',
    openedFrom: null,        // 'history' while a scan that was opened from the History view is showing (for the breadcrumbs)
    settingsSection: null,   // which Settings panel a link pointed at ('scanning', 'appearance', 'output') or null
    activeTab: 'ALL',        // which company the main pane shows: 'ALL' or a domain
};

const listeners = new Map();
export function on(event, handler) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(handler);
    return () => listeners.get(event).delete(handler);
}
export function emit(event, detail) {
    (listeners.get(event) || []).forEach(handler => { try { handler(detail); } catch (error) { console.error(event, error); } });
}

export const byDomain = domain => state.companies.find(c => c.domain === domain);
export const totalJobs = () => state.companies.reduce((n, c) => n + (c.jobs_count || 0), 0);
