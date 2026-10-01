// The queue panel beside the rail: open/closed from the rail's Queue button, remembered in settings.
// It only shows on the Scraper view. Below 1200px wide it is a drawer over the workspace (closed at launch, not remembered),
// and closes on Esc or a click outside, handing focus back to the Queue button.
import { state, on } from './state.js';
import { setSetting } from './settings.js';
import { $ } from './util.js';

export const NARROW = '(max-width: 1199px)';
let drawerOpen = false;
let showView = () => {};

const isNarrow = () => window.matchMedia(NARROW).matches;
const wantsOpen = () => isNarrow() ? drawerOpen : state.settings?.queue_panel_open !== false;
export const panelVisible = () => state.activeView === 'scraper' && wantsOpen();

export function paintQueuePanel() {
    const visible = panelVisible();
    $('#queuePanel').hidden = !visible;
    $('#app').dataset.queue = visible ? 'on' : 'off';
    $('#queueToggle').setAttribute('aria-pressed', String(visible));
}

function setOpen(open) {
    if (isNarrow()) { drawerOpen = open; paintQueuePanel(); }
    else setSetting('queue_panel_open', open);                 // repaints through the 'settings' event
}

export function toggleQueuePanel() {
    if (state.activeView !== 'scraper') { showView('scraper'); setOpen(true); return; }
    setOpen(!wantsOpen());
}

function closeDrawer({ refocus }) {
    if (!isNarrow() || !drawerOpen || state.activeView !== 'scraper') return;
    const hadFocus = $('#queuePanel').contains(document.activeElement);
    setOpen(false);
    if (refocus || hadFocus) $('#queueToggle').focus();
}

export function initQueuePanel(showViewFn) {
    showView = showViewFn;
    $('#queueToggle').addEventListener('click', toggleQueuePanel);
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && isNarrow() && drawerOpen) closeDrawer({ refocus: true }); });
    document.addEventListener('mousedown', event => {
        if (!$('#queuePanel').contains(event.target) && !$('#queueToggle').contains(event.target)) closeDrawer({ refocus: false });
    });
    window.matchMedia(NARROW).addEventListener('change', () => { drawerOpen = false; paintQueuePanel(); });
    on('settings', paintQueuePanel);
    on('view', paintQueuePanel);
    paintQueuePanel();
}
