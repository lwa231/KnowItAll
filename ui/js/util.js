// Small helpers shared by every module. No state, no DOM ownership.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ESCAPES[c]);
export const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
export const fmtNum = value => Number(value || 0).toLocaleString('en-US');
export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export function relTime(value, now = Date.now()) {
    if (!value) return '—';
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date)) return '—';
    const mins = Math.floor((now - date.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    if (days < 30) return `${Math.floor(days / 7)}w ago`;
    if (days < 365) return `${Math.floor(days / 30)}mo ago`;
    return `${Math.floor(days / 365)}y ago`;
}

export function fmtBytes(bytes) {
    let size = Number(bytes || 0);
    for (const unit of ['B', 'KB', 'MB', 'GB']) {
        if (size < 1024 || unit === 'GB') return unit === 'B' ? `${size} B` : `${size.toFixed(size < 10 ? 1 : 0)} ${unit}`;
        size /= 1024;
    }
}

export function debounce(fn, ms) {
    let timer = null;
    const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
    wrapped.cancel = () => clearTimeout(timer);
    wrapped.flush = (...args) => { clearTimeout(timer); fn(...args); };
    return wrapped;
}

/** Like debounce, but runs at most once per `ms` and always delivers the last call. */
export function throttle(fn, ms) {
    let last = 0, timer = null, pending = null;
    return (...args) => {
        pending = args;
        const wait = ms - (Date.now() - last);
        if (wait <= 0) { last = Date.now(); fn(...pending); return; }
        if (!timer) timer = setTimeout(() => { timer = null; last = Date.now(); fn(...pending); }, wait);
    };
}

export function svgIcon(name, label = '') {
    return `<svg ${label ? `role="img" aria-label="${esc(label)}"` : 'aria-hidden="true"'}><use href="#i-${name}"/></svg>`;
}

/** Display name for an ISO-3166 country code ("DE" -> "Germany"); falls back to the code. */
const regionNames = (() => { try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; } })();
export function countryName(code) {
    if (!code) return 'Unknown';
    try { return regionNames?.of(code) || code; } catch { return code; }
}
