// The only module that talks to the server. Every call carries the per-launch token the page was served with.
import { sleep } from './util.js';

const TOKEN = document.querySelector('meta[name="knowitall-token"]').content;
const HEADERS = { 'X-KnowItAll-Token': TOKEN };

export class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
}

async function request(path, options = {}) {
    const response = await fetch(path, options);
    let data = null;
    try { data = await response.json(); } catch { /* not JSON */ }
    if (!response.ok) throw new ApiError(response.status, (data && data.error) || `HTTP ${response.status}`);
    return data;
}

export const get = (path, signal) => request(path, { headers: HEADERS, signal });
export const post = (path, body) => request(path, {
    method: 'POST', headers: { ...HEADERS, 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
});

/**
 * Live state from the server: server-sent events over fetch (EventSource cannot send the token header).
 * Reconnects with backoff, resuming from the last cursor; after repeated failures it falls back to polling
 * /api/state until the stream works again. `onState(state)` receives every message; `onStatus` is one of
 * 'live' | 'reconnecting' | 'polling'.
 */
export function connect({ getCursor, onState, onStatus }) {
    let closed = false, controller = null, failures = 0;

    async function readStream() {
        controller = new AbortController();
        const response = await fetch(`/api/stream?since=${getCursor()}`, { headers: HEADERS, signal: controller.signal });
        if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
        onStatus('live');
        failures = 0;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
            const { done, value } = await reader.read();
            if (done) return;
            buffer += decoder.decode(value, { stream: true });
            let end;
            while ((end = buffer.indexOf('\n\n')) >= 0) {
                const chunk = buffer.slice(0, end);
                buffer = buffer.slice(end + 2);
                const line = chunk.split('\n').find(l => l.startsWith('data: '));
                if (line) onState(JSON.parse(line.slice(6)));
            }
        }
    }

    (async function loop() {
        while (!closed) {
            try { await readStream(); } catch (error) { if (closed) return; }
            if (closed) return;
            failures += 1;
            onStatus(failures >= 3 ? 'polling' : 'reconnecting');
            if (failures >= 3) {
                try { onState(await get(`/api/state?since=${getCursor()}`)); } catch { /* still down */ }
                await sleep(1000);
            } else {
                await sleep(Math.min(5000, 400 * 2 ** failures));
            }
        }
    })();

    return { close() { closed = true; controller?.abort(); } };
}
