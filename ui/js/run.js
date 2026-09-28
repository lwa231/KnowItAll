// Starting and stopping scans, and the header's live numbers (state label, jobs found, elapsed, progress).
import * as api from './api.js';
import { state, totalJobs } from './state.js';
import { runOptions, bindSwitch, bindNumber } from './settings.js';
import { logLine, announce, toast } from './notify.js';
import { $, fmtNum } from './util.js';

const addresses = () => $('#addressInput').value.split(/[\s,]+/).filter(Boolean);

export async function startRun() {
    const urls = addresses();
    $('#addressInput').value = '';
    try {
        const result = await api.post('/api/run', { urls, options: runOptions() });
        if (!result.started) toast(urls.length ? 'Those do not look like web addresses' : 'Add a company web address first', { kind: 'bad' });
    } catch (error) { toast('Could not reach the app', { kind: 'bad' }); }
}

const stopRun = () => api.post('/api/stop').catch(() => {});
const toggle = () => (state.running ? stopRun() : startRun());

export function paintRunState() {
    const scanning = state.companies.filter(c => c.state === 'scanning').length;
    const runBtn = $('#runBtn'), label = $('#processLabel');
    const offline = state.connection === 'reconnecting' || state.connection === 'polling';
    if (state.running) {
        $('#pixelChar').className = 'pixel-char char-active';
        label.textContent = 'Scanning'; label.style.color = '';
        $('#processSub').textContent = scanning === 1 ? '1 company' : `${scanning} companies`;
        runBtn.textContent = 'Stop';
        runBtn.className = 'btn btn-stop btn-lg';
        $('#processToggle').setAttribute('aria-label', 'Stop scanning');
    } else {
        $('#pixelChar').className = 'pixel-char char-idle';
        label.textContent = offline ? 'Reconnecting' : 'Idle'; label.style.color = offline ? 'var(--red)' : 'var(--text-muted)';
        const stopped = state.companies.some(c => c.state === 'stopped');
        const failed = state.companies.some(c => c.state === 'failed');
        $('#processSub').textContent = !state.companies.length ? 'ready' : stopped ? 'stopped' : failed ? 'finished with errors' : 'complete';
        runBtn.textContent = 'Start';
        runBtn.className = 'btn btn-primary btn-lg';
        $('#processToggle').setAttribute('aria-label', 'Start scanning');
    }
}

let shown = 0;
export function paintMetrics() {
    $('#jobsFound').textContent = fmtNum(totalJobs());
    const total = state.companies.length || 1;
    const finished = state.companies.filter(c => ['done', 'failed', 'stopped'].includes(c.state)).length;
    const target = Math.round((finished / total) * 100);
    $('#progressFill').style.width = `${target}%`;
    $('#progressValue').textContent = `${target}%`;
    $('#progressTrack').setAttribute('aria-valuenow', String(target));
    shown = target;
}

export function announceRunChange(wasRunning) {
    if (state.running && !wasRunning) announce('Scan started');
    if (!state.running && wasRunning) {
        const done = state.companies.filter(c => c.state === 'done').length;
        announce(`Scan finished: ${done} of ${state.companies.length} companies complete, ${fmtNum(totalJobs())} postings`);
    }
}

export function initRun() {
    $('#runForm').addEventListener('submit', event => { event.preventDefault(); toggle(); });
    $('#processToggle').addEventListener('click', toggle);
    $('#addBtn').addEventListener('click', async () => {
        const urls = addresses();
        if (!urls.length) return;
        $('#addressInput').value = '';
        const { added } = await api.post('/api/queue', { urls });
        if (!added.length) toast('Already in the queue, or not a web address', { kind: 'bad' });
    });
    $('#quitBtn').addEventListener('click', async () => {
        logLine('quitting…');
        try { await api.post('/api/quit'); } catch { /* the window closes */ }
        setTimeout(() => window.close(), 300);
    });
    bindSwitch($('#freshSwitch'), 'fresh');
    bindNumber($('#hdrMaxJobs'), 'max_jobs');
    bindNumber($('#hdrDepth'), 'max_enrich');
    setInterval(() => {
        if (!state.running) return;
        state.elapsed += 1;
        $('#elapsed').textContent = `${String(Math.floor(state.elapsed / 60)).padStart(2, '0')}:${String(state.elapsed % 60).padStart(2, '0')}`;
    }, 1000);
}
