// Ways of telling the person something: the activity log, toasts, and the screen-reader announcer.
import { $, esc } from './util.js';

const activityBody = () => $('#activityBody');

export function logLine(text) {
    $('#activityLatest').textContent = text;
    const body = activityBody();
    const line = document.createElement('div');
    line.textContent = `› ${text}`;
    body.appendChild(line);
    while (body.childElementCount > 500) body.removeChild(body.firstChild);
    body.scrollTop = body.scrollHeight;
}

/** Say something to screen readers without changing what is on screen. */
let announceTimer = null;
export function announce(text) {
    const region = $('#announcer');
    clearTimeout(announceTimer);
    region.textContent = '';
    announceTimer = setTimeout(() => { region.textContent = text; }, 60);      // a change is needed for it to be read
}

/** A short confirmation. Stays while hovered or focused; `kind: 'bad'` marks a failure. */
export function toast(message, { kind = 'ok', ms = 5000 } = {}) {
    const box = $('#toasts');
    const node = document.createElement('div');
    node.className = `toast ${kind === 'bad' ? 'bad' : ''}`;
    node.innerHTML = `<span>${esc(message)}</span>`;
    let timer = null, paused = false;
    const remove = () => node.remove();
    const arm = () => { clearTimeout(timer); timer = setTimeout(() => { if (!paused) remove(); }, ms); };
    node.addEventListener('mouseenter', () => { paused = true; clearTimeout(timer); });
    node.addEventListener('mouseleave', () => { paused = false; arm(); });
    box.appendChild(node);
    while (box.childElementCount > 4) box.removeChild(box.firstChild);
    arm();
    logLine(message);
    return node;
}
