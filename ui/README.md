# The KnowItAll interface

Plain HTML, CSS and ES modules. **No build step, no CDN, no framework**: the folder is served as it is by the
app's local server (`knowitall/server.py`), works offline, and can be edited in any text editor. Reload the window
(or the page in a browser) to see a change.

```
index.html          the page: landmarks, the header + filter bar, every view's markup, the icon sprite
css/
  tokens.css        ALL colours, type sizes, spacing, timings (dark + light). Restyle here.
  base.css          reset, typography, focus ring, utilities (.sr-only, .label ...), reduced motion
  controls.css      buttons, inputs, switches, segmented control, chips, popovers, badges, toasts
  layout.css        app frame: sidebar, header, filter bar, notice bar, activity strip, responsive rules
  workspace.css     tab strip, dock tiles, floating windows, drag guides
  table.css         the postings table, skeleton, empty states, column shedding
  views.css         history / output / settings / system panels
  fonts.css         @font-face for the bundled Inter and Fira Code (../fonts, SIL OFL)
js/
  main.js           boot: settings, wiring, the live stream, view switching
  api.js            the only code that talks to the server (token header, event stream, reconnect)
  state.js          the shared state object + tiny publish/subscribe (on / emit)
  settings.js       settings kept on the server (settings.json); control bindings
  filters.js        the filter state and how it becomes a /api/jobs query (no DOM)
  filterbar.js      the header's search box, Listed/Gone/Closed switch, facet chips and popovers
  jobs.js           PaneData: rows, total, sorting and paging for one pane (no DOM)
  table.js          PaneData -> HTML (table, skeleton, empty/error states)
  workspace.js      tabs, dock quadrants, floating windows, drag, the Move menu, infinite scroll
  queue.js run.js notices.js notify.js popover.js util.js
  views/            history.js output.js settings.js system.js
tests/run.html      in-browser tests of the pure logic: open /ui/tests/run.html while the app runs
```

## How data flows

1. `api.connect()` opens `/api/stream` (server-sent events over `fetch`, because `EventSource` cannot send the
   token header). Each message is the whole state: companies, notices, run status, and the events since the last
   one. It reconnects with backoff and falls back to polling `/api/state`.
2. `main.js` stores that in `state` and tells the modules (`queue`, `run`, `notices`, `workspace`).
3. **Rows are never pushed.** Events only say "company X has more postings". Each pane asks the server for what it
   shows (`/api/jobs`, 100 at a time) through `PaneData`, so filtering, sorting and counting happen in SQLite.
4. Changing a filter fires `filters`; the filter bar repaints, the panes refetch, the chips refetch their counts.

## Conventions

- **Colours and sizes come from `tokens.css`.** Component CSS uses `var(--...)`; `tests/test_ui_static.py` fails
  on a raw colour anywhere else, and `tests/test_ui_contrast.py` checks every text token against every surface in
  both themes (WCAG AA, 4.5:1). Nothing is smaller than 12px.
- **Real elements.** Anything clickable is a `<button>` (with `type="button"`); switches are
  `role="switch"`; the tab strip is a roving-focus `tablist`; popovers trap focus, close on Escape and give focus
  back. Everything that can be dragged can also be moved from the pane's "Move" menu.
- **Never put user data in markup unescaped**: use `esc()` (see `table.js`).
- **Motion is short and optional**: `prefers-reduced-motion` switches it off in `base.css`.
- No emoji as icons: use the sprite in `index.html` (`svgIcon('name')`).

## Common changes

| To... | Do this |
|---|---|
| change the palette or type sizes | edit `css/tokens.css`, then run `pytest tests/test_ui_contrast.py` |
| add a filter chip | add its key to `LIST_KEYS` in `filters.js` (and the server's `LIST_FILTERS`), add a spec in `chipSpecs()` in `filterbar.js` |
| add a table column | add it to `COLUMNS` and `rowHTML` in `table.js`; decide at which pane width it is dropped (`table.css`, thresholds in `workspace.js`) |
| add a view | add a `<section class="view" data-view="x">` and a nav button in `index.html`, then a module in `js/views/` and a line in `main.js` `showView()` |
| add a setting | add it to `knowitall/settings.py` (default + validation), then bind a control with `bindSwitch` / `bindNumber` |
| add a server call | add the route in `knowitall/server.py` (it goes through `knowitall/service.py`, so the CLI can offer it too) |

## Checking your work

```bash
pytest                      # includes the token contrast and static UI checks
# in the running app, open  /ui/tests/run.html  - the tab title reads "PASS 26/26"
```
