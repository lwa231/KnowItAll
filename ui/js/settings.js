// Settings live on the server (settings.json), so they survive restarts and are shared with the command line.
// Changes apply at once here and are saved a moment later; if the server refuses, the saved value comes back.
import * as api from './api.js';
import { state, emit } from './state.js';
import { $, debounce } from './util.js';
import { toast } from './notify.js';

export const DEFAULTS = { theme: 'dark', max_jobs: 2000, max_enrich: 50, concurrency: 3, parallel_mode: 'auto', cache_reuse: '12h', autosave: true, auto_backup: true, export_scope: 'matching', browser_workers: 1, browser_workers_auto: true, time_limit_min: 3, queue_panel_open: true };

export async function loadSettings() {
    try { state.settings = await api.get('/api/settings'); }
    catch { state.settings = { ...DEFAULTS }; }
    applyTheme();
    emit('settings');
}

export function applyTheme() {
    document.documentElement.dataset.theme = state.settings.theme === 'light' ? 'light' : 'dark';
}

let pending = {};
const flush = debounce(async () => {
    const patch = pending;
    pending = {};
    try {
        state.settings = await api.post('/api/settings', patch);
        emit('settings');
    } catch (error) {
        toast(`Could not save settings: ${error.message}`, { kind: 'bad' });
        await loadSettings();                     // show what is really saved
    }
}, 300);

export function setSetting(key, value) {
    state.settings = { ...state.settings, [key]: value };
    pending[key] = value;
    if (key === 'theme') applyTheme();
    emit('settings');
    flush();
}

/** What a scan should be started with. */
export const runOptions = () => ({
    cache_reuse: state.settings.cache_reuse, max_jobs: state.settings.max_jobs, max_enrich: state.settings.max_enrich,
    concurrency: state.settings.concurrency, autosave: !!state.settings.autosave, time_limit_min: state.settings.time_limit_min,
});

/* ---- two-way bindings between controls and settings ---- */
const bindings = [];
export function bindSwitch(button, key, { invert = false } = {}) {
    const paint = () => button.setAttribute('aria-checked', String(invert ? !state.settings[key] : !!state.settings[key]));
    button.addEventListener('click', () => setSetting(key, !state.settings[key]));
    bindings.push(paint);
    paint();
}
export function bindThemeSwitch(button) {
    const paint = () => button.setAttribute('aria-checked', String(state.settings.theme === 'light'));
    button.addEventListener('click', () => setSetting('theme', state.settings.theme === 'light' ? 'dark' : 'light'));
    bindings.push(paint);
    paint();
}
/** A segmented control (role=radiogroup of buttons with data-value) bound to one setting. */
export function bindSegmented(group, key, parse = String) {
    const paint = () => group.querySelectorAll('[data-value]').forEach(button => {
        const on = parse(button.dataset.value) === state.settings[key];
        button.setAttribute('aria-checked', String(on));
        button.tabIndex = on ? 0 : -1;
    });
    group.addEventListener('click', event => {
        const button = event.target.closest('[data-value]');
        if (button) setSetting(key, parse(button.dataset.value));
    });
    group.addEventListener('keydown', event => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const buttons = [...group.querySelectorAll('[data-value]')];
        const at = buttons.findIndex(b => parse(b.dataset.value) === state.settings[key]);
        const next = buttons[(at + step + buttons.length) % buttons.length];
        setSetting(key, parse(next.dataset.value));
        next.focus();
    });
    bindings.push(paint);
    paint();
}
/** Register a function that repaints a control from state.settings. */
export function addBinding(paint) { bindings.push(paint); }
export function syncBindings() { bindings.forEach(paint => paint()); }
