// Settings live on the server (settings.json), so they survive restarts and are shared with the command line.
// Changes apply at once here and are saved a moment later; if the server refuses, the saved value comes back.
import * as api from './api.js';
import { state, emit } from './state.js';
import { $, debounce } from './util.js';
import { toast } from './notify.js';

export const DEFAULTS = { theme: 'dark', max_jobs: 2000, max_enrich: 50, concurrency: 3, fresh: false, autosave: true, browser_workers: 1, time_limit_min: 3 };

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
    fresh: !!state.settings.fresh, max_jobs: state.settings.max_jobs, max_enrich: state.settings.max_enrich,
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
export function bindNumber(input, key) {
    const paint = () => { if (document.activeElement !== input) input.value = state.settings[key]; };
    input.addEventListener('change', () => {
        const min = Number(input.min || 0), max = Number(input.max || Infinity);
        const n = parseInt(input.value, 10);
        if (Number.isNaN(n)) { input.value = state.settings[key]; return; }
        setSetting(key, Math.max(min, Math.min(max, n)));
        input.value = state.settings[key];
    });
    bindings.push(paint);
    paint();
}
export function syncBindings() { bindings.forEach(paint => paint()); }
