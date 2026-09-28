// One floating panel at a time, anchored to a button. Handles positioning, Escape, outside clicks and focus.

let current = null;

export function closePopover() {
    if (!current) return;
    const { panel, anchor, onClose, returnFocus } = current;
    current = null;
    panel.remove();
    anchor.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('keydown', keys, true);
    window.removeEventListener('resize', closePopover);
    if (onClose) onClose();
    if (returnFocus) anchor.focus();
}

function outside(event) {
    if (!current) return;
    if (current.panel.contains(event.target) || current.anchor.contains(event.target)) return;
    current.returnFocus = false;
    closePopover();
}

function keys(event) {
    if (!current) return;
    if (event.key === 'Escape') { event.stopPropagation(); closePopover(); return; }
    if (event.key === 'Tab') {                           // keep keyboard focus inside the panel while it is open
        const items = [...current.panel.querySelectorAll('button, input, [tabindex="0"]')].filter(el => !el.disabled && el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
}

function place(panel, anchor) {
    const a = anchor.getBoundingClientRect();
    panel.style.visibility = 'hidden';
    panel.style.left = '0px'; panel.style.top = '0px';
    const p = panel.getBoundingClientRect();
    const margin = 8;
    let left = Math.min(a.left, window.innerWidth - p.width - margin);
    left = Math.max(margin, left);
    let top = a.bottom + 6;
    if (top + p.height > window.innerHeight - margin) top = Math.max(margin, a.top - p.height - 6);   // flip above
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.visibility = '';
}

/**
 * Open a panel under `anchor`. `render(panel, close)` fills it and may return the element to focus first.
 * Returns a handle with `refresh()` to re-render in place (e.g. when new counts arrive).
 */
export function openPopover({ anchor, label, render, onClose, width }) {
    closePopover();
    const panel = document.createElement('div');
    panel.className = 'popover';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', label);
    if (width) panel.style.width = `${width}px`;
    document.body.appendChild(panel);
    anchor.setAttribute('aria-expanded', 'true');
    current = { panel, anchor, onClose, returnFocus: true };
    const fill = () => {
        const scroll = panel.querySelector('.popover-body')?.scrollTop || 0;
        const focusTarget = render(panel, closePopover);
        const body = panel.querySelector('.popover-body');
        if (body) body.scrollTop = scroll;
        return focusTarget;
    };
    const target = fill();
    place(panel, anchor);
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', keys, true);
    window.addEventListener('resize', closePopover);
    (target || panel.querySelector('button, input'))?.focus();
    return { refresh: () => { if (current && current.panel === panel) { const active = document.activeElement; fill(); if (active?.id) panel.querySelector(`#${CSS.escape(active.id)}`)?.focus(); } } };
}

export const isOpen = anchor => !!current && current.anchor === anchor;
