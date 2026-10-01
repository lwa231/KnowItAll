// Adding and stopping scans, and the header's live numbers (state label, jobs found, elapsed, progress).
//
// Enter in the address box only ever adds: in Auto mode the address starts scanning as soon as a slot is free, in Manual
// mode it joins the list and Start runs the list. Stop is its own button and never the side effect of typing.
import * as api from './api.js';
import { state, totalJobs, on } from './state.js';
import { counts } from './counts.js';
import { isFiltering } from './filters.js';
import { setSetting } from './settings.js';
import { logLine, announce, toast } from './notify.js';
import { $, $$, fmtNum } from './util.js';

const addresses = () => $('#addressInput').value.split(/[\s,]+/).filter(Boolean);
const isManual = () => state.settings?.parallel_mode === 'manual';
const readyCount = () => state.companies.filter(c => c.state === 'ready').length;
let stopping = false;

export async function submitAddresses() {
    const urls = addresses();
    if (!urls.length) { toast('Add a company web address first', { kind: 'bad' }); return; }
    try {
        const result = await api.post('/api/scan', { urls });
        if (result.ignored?.length) {
            const first = result.ignored[0];
            toast(result.ignored.length === 1 ? `Already scanning ${first}.` : `Already scanning ${first} and ${result.ignored.length - 1} more.`);
        }
        if (!result.added.length && !result.ignored?.length) { toast('Those do not look like web addresses', { kind: 'bad' }); return; }
        $('#addressInput').value = '';                          // cleared after every successful submit
    } catch (error) { toast(`Could not add that: ${error.message || 'the app did not answer'}`, { kind: 'bad' }); }
}

async function startReady() {
    try { await api.post('/api/start'); } catch (error) { toast(`Could not start: ${error.message}`, { kind: 'bad' }); }
}

async function stopAll() {
    if (stopping) return;
    stopping = true;
    paintRunState();
    setTimeout(() => { stopping = false; paintRunState(); }, 1000);          // ignores repeat clicks for a second
    try { await api.post('/api/stop'); } catch { /* the stream shows the real state */ }
}

async function setAtOnce(n) {
    if (state.settings.concurrency === n) return;
    state.settings = { ...state.settings, concurrency: n };                  // shown at once; the server applies it to the running scans
    paintRunState();
    try { await api.post('/api/parallel', { n }); } catch (error) { toast(`Could not change that: ${error.message}`, { kind: 'bad' }); }
}

export function paintRunState() {
    const scanning = state.companies.filter(c => c.state === 'scanning').length;
    const waiting = state.companies.filter(c => c.state === 'waiting').length;
    const ready = readyCount();
    const label = $('#processLabel');
    const offline = state.connection === 'reconnecting' || state.connection === 'polling';
    $('#processToggle').classList.toggle('running', state.running);        // orange while the queue is active (layout.css)
    if (state.running) {
        $('#pixelChar').className = 'pixel-char char-active';
        label.textContent = 'Scanning'; label.style.color = '';
        $('#processSub').textContent = `${scanning} scanning${waiting ? ` · ${waiting} waiting` : ''}`;
        $('#processToggle').setAttribute('aria-label', 'Stop scanning');
    } else {
        $('#pixelChar').className = 'pixel-char char-idle';
        label.textContent = offline ? 'Reconnecting' : 'Idle'; label.style.color = offline ? 'var(--red)' : '';
        const stopped = state.companies.some(c => c.state === 'stopped');
        const failed = state.companies.some(c => c.state === 'failed');
        $('#processSub').textContent = ready ? `${ready} ready` : !state.companies.length ? 'ready' : stopped ? 'stopped' : failed ? 'finished with errors' : 'complete';
        $('#processToggle').setAttribute('aria-label', 'Scanner status');
    }
    $('#runBtn').textContent = isManual() ? 'Add' : 'Scan';
    const start = $('#startBtn');
    start.hidden = !isManual() || ready === 0;
    start.textContent = `Start (${ready} ready)`;
    const stop = $('#stopBtn');
    stop.hidden = !state.running;
    stop.disabled = stopping;
    stop.textContent = stopping ? 'Stopping…' : 'Stop';
    $$('#atOnceSeg button').forEach(button => {
        const on = Number(button.dataset.atOnce) === state.settings?.concurrency;
        button.setAttribute('aria-checked', String(on));
        button.tabIndex = on ? 0 : -1;
    });
}

export function paintMetrics() {
    // With filters on, the header counts what matches (and says how many there are in all); without, what was found.
    const narrowed = isFiltering() && counts.ready && counts.filtering;
    $('#jobsFoundLabel').textContent = isFiltering() ? 'Matching' : 'Jobs found';
    $('#jobsFound').textContent = fmtNum(narrowed ? counts.matching : totalJobs());
    $('#jobsTotal').hidden = !narrowed;
    $('#jobsTotal').textContent = narrowed ? `of ${fmtNum(counts.total)}` : '';
    // Progress counts the companies in play this session; one that is only waiting for a slot is not "0% done".
    const inPlay = state.companies.filter(c => c.state !== 'ready');
    const finished = inPlay.filter(c => ['done', 'failed', 'stopped'].includes(c.state)).length;
    const target = inPlay.length ? Math.round((finished / inPlay.length) * 100) : 0;
    $('#progressFill').style.width = `${target}%`;
    $('#progressValue').textContent = `${target}%`;
    $('#progressTrack').setAttribute('aria-valuenow', String(target));
}

export function announceRunChange(wasRunning) {
    if (state.running && !wasRunning) announce('Scan started');
    if (!state.running && wasRunning) {
        stopping = false;
        const done = state.companies.filter(c => c.state === 'done').length;
        const narrowed = isFiltering() && counts.ready && counts.filtering;
        announce(`Scan finished: ${done} of ${state.companies.length} companies complete, ${narrowed ? `${fmtNum(counts.matching)} matching postings of ${fmtNum(counts.total)}` : `${fmtNum(totalJobs())} postings`}`);
    }
}

export function initRun() {
    $('#runForm').addEventListener('submit', event => { event.preventDefault(); submitAddresses(); });
    $('#startBtn').addEventListener('click', startReady);
    $('#stopBtn').addEventListener('click', stopAll);
    $('#processToggle').addEventListener('click', () => { if (state.running) stopAll(); else $('#addressInput').focus(); });
    $('#atOnceSeg').addEventListener('click', event => {
        const button = event.target.closest('[data-at-once]');
        if (button) setAtOnce(Number(button.dataset.atOnce));
    });
    $('#atOnceSeg').addEventListener('keydown', event => {
        const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = Math.max(1, Math.min(3, state.settings.concurrency + step));
        setAtOnce(next);
        $(`#atOnceSeg [data-at-once="${next}"]`).focus();
    });
    $('#quitBtn').addEventListener('click', async () => {
        logLine('quitting…');
        try { await api.post('/api/quit'); } catch { /* the window closes */ }
        setTimeout(() => window.close(), 300);
    });
    on('counts', paintMetrics);
    on('filters', paintMetrics);
    setInterval(() => {
        if (!state.running) return;
        state.elapsed += 1;
        $('#elapsed').textContent = `${String(Math.floor(state.elapsed / 60)).padStart(2, '0')}:${String(state.elapsed % 60).padStart(2, '0')}`;
    }, 1000);
}
