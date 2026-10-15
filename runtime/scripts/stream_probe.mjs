// Real run of the game stream (Play in Blender): a copy of the demo game, its own dev server, the
// stream host (@gamestudio/devserver's bin/gamestudio-stream.js) and this script as the Blender end
// of the TCP connection, for `--seconds` (10). Reports the frame rate, frame latency, input round
// trip, CPU use of the host and its browser, and whether sound reaches the default output device.
// Then, unless --no-blender, background Blender plays the same game through
// blender/gamestudio/core/stream.py (runtime/scripts/stream_probe_blender.py): decode cost, poll()
// cost and the round trip to decoded pixels.
//
//   node runtime/scripts/stream_probe.mjs [--seconds 10] [--width 960] [--height 540]
//        [--mode headless|offscreen] [--max-fps 60] [--no-blender] [--no-audio]
//
// Uses a throw-away project in runtime/.smoke/stream-probe (never examples/demo-game), whose page
// toggles a 48x48 marker in the top-left corner on F8. Frames go to GS_SMOKE_OUT (default
// runtime/.smoke/stream-probe-out): JPEGs as sent, PNGs as Blender decoded them. The audio check
// plays a quiet 1 s tone (Windows: read with the Core Audio session meters).
// Env: BLENDER, GS_BROWSER (found by programs.mjs).

import { execFile, spawn } from 'node:child_process';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import { MessageReader, decodeFrame, encodeJson } from '../packages/devserver/src/stream-protocol.js';
import { findBlender, findBrowser } from './programs.mjs';

const RUNTIME = fileURLToPath(new URL('..', import.meta.url));
const REPO = path.resolve(RUNTIME, '..');
const DEMO = path.join(REPO, 'examples', 'demo-game');
const PROJECT = path.join(RUNTIME, '.smoke', 'stream-probe');
const OUT = path.resolve(process.env.GS_SMOKE_OUT ?? path.join(RUNTIME, '.smoke', 'stream-probe-out'));
const HOST = path.join(RUNTIME, 'packages', 'devserver', 'bin', 'gamestudio-stream.js');
const argv = process.argv.slice(2);
const option = (name, fallback) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback);
const SECONDS = Number(option('--seconds', 10));
const WIDTH = Number(option('--width', 960));
const HEIGHT = Number(option('--height', 540));
const MODE = option('--mode', 'headless');
const MAX_FPS = option('--max-fps', '60');
const WINDOWS = process.platform === 'win32';
/** PowerShell without blocking the event loop (frames keep arriving while it runs). */
const powershell = async (args) => (await promisify(execFile)('powershell.exe', ['-NoProfile', ...args], { encoding: 'utf8' })).stdout;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const q = (xs, p) => (xs.length ? +[...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(xs.length * p))].toFixed(1) : null);
const MARKER = `
    <div id="gs-probe-marker" style="position:fixed;left:0;top:0;width:48px;height:48px;background:#000;z-index:99999;pointer-events:none"></div>
    <script>
      // stream_probe.mjs: F8 toggles the marker; Date.now() of the key and of the next frame.
      window.__probe = { toggles: 0 };
      addEventListener('keydown', (e) => {
        if (e.code !== 'F8') return;
        const m = document.getElementById('gs-probe-marker');
        const on = !window.__probe.on;
        m.style.background = on ? '#fff' : '#000';
        window.__probe = { toggles: window.__probe.toggles + 1, on, downAt: Date.now() };
        requestAnimationFrame(() => { window.__probe.rafAt = Date.now(); });
      }, true);
    </script>
  </body>`;

/** Copies the demo's sources and its export into PROJECT, with the marker in index.html. */
async function prepareProject() {
  await rm(PROJECT, { recursive: true, force: true });
  for (const item of ['vite.config.ts', 'src']) await cp(path.join(DEMO, item), path.join(PROJECT, item), { recursive: true });
  const html = await readFile(path.join(DEMO, 'index.html'), 'utf8');
  await writeFile(path.join(PROJECT, 'index.html'), html.replace('</body>', MARKER.trimStart()));
  const config = JSON.parse(await readFile(path.join(DEMO, 'gamestudio.json'), 'utf8'));
  await writeFile(path.join(PROJECT, 'gamestudio.json'), JSON.stringify({ ...config, link: { editorPort: 0, gamePort: 0 } }, null, 2));
  await cp(path.join(DEMO, '.gamestudio', 'export'), path.join(PROJECT, '.gamestudio', 'export'), { recursive: true });
}

/** CPU seconds of the host and of every browser process with a stream profile (Windows). */
async function cpuSeconds(hostPid) {
  if (!WINDOWS) return null;
  const script = `$all = Get-CimInstance Win32_Process; $b = 0; foreach ($p in $all) { if ($p.CommandLine -like '*gamestudio-stream-*' -and $p.Name -notlike 'node*') { $b += $p.UserModeTime + $p.KernelModeTime } }; $h = ($all | Where-Object ProcessId -eq ${hostPid}); Write-Output "$($h.UserModeTime + $h.KernelModeTime) $b"`;
  const [host, browser] = (await powershell(['-Command', script])).trim().split(/\s+/).map(Number);
  return { host: host / 1e7, browser: browser / 1e7, at: Date.now() };
}

/** Browser processes still running with a stream profile (after stop: should be none). */
async function leftoverBrowsers() {
  if (!WINDOWS) return null;
  const out = await powershell(['-Command', "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*gamestudio-stream-*' -and $_.Name -notlike 'node*' -and $_.Name -notlike 'powershell*' }).Count"]);
  return Number(out.trim() || 0);
}

// Peak meters of the default output device's audio sessions (pid -> peak), via Core Audio.
const PEAKS_PS = String.raw`
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int NotImpl1(); [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { [PreserveSig] int Activate(ref Guid iid, int dwClsCtx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 { int NotImpl1(); int NotImpl2(); [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator e); }
[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator { [PreserveSig] int GetCount(out int n); [PreserveSig] int GetSession(int i, out IAudioSessionControl2 s); }
[Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 { int N0(); int N1(); int N2(); int N3(); int N4(); int N5(); int N6(); int N7(); int N8();
  [PreserveSig] int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string s);
  [PreserveSig] int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string s);
  [PreserveSig] int GetProcessId(out int pid); }
[Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioMeterInformation { [PreserveSig] int GetPeakValue(out float peak); }
public static class Peaks { public static string Read() {
  var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator()); IMMDevice dev; en.GetDefaultAudioEndpoint(0, 1, out dev);
  Guid iid = typeof(IAudioSessionManager2).GUID; object o; dev.Activate(ref iid, 23, IntPtr.Zero, out o);
  IAudioSessionEnumerator se; ((IAudioSessionManager2)o).GetSessionEnumerator(out se); int n; se.GetCount(out n);
  var sb = new System.Text.StringBuilder();
  for (int i = 0; i < n; i++) { IAudioSessionControl2 s; se.GetSession(i, out s); int pid; s.GetProcessId(out pid); float p; ((IAudioMeterInformation)s).GetPeakValue(out p); sb.Append(pid + " " + p.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture) + ";"); }
  return sb.ToString(); } }
"@
$ids = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*gamestudio-stream-*' } | ForEach-Object { [string]$_.ProcessId })
$max = 0.0
for ($i = 0; $i -lt 6; $i++) { foreach ($e in [Peaks]::Read().Split(';')) { $f = $e.Split(' '); if ($f.Length -eq 2 -and $ids -contains $f[0]) { $v = [double]::Parse($f[1], [Globalization.CultureInfo]::InvariantCulture); if ($v -gt $max) { $max = $v } } }; Start-Sleep -Milliseconds 120 }
Write-Output $max`;

async function main() {
  await mkdir(OUT, { recursive: true });
  await prepareProject();
  if (!findBrowser()) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');
  const server = await createServer({
    root: PROJECT, cacheDir: path.join(PROJECT, '.vite-cache'), logLevel: process.env.GS_VITE_LOG ?? 'warn', server: { port: 5195, strictPort: false },
  });
  await server.listen();
  const url = server.resolvedUrls.local[0];
  console.log(`dev server: ${url}`);
  /** @type {any} */
  const report = { mode: MODE, size: [WIDTH, HEIGHT], max_fps: Number(MAX_FPS) };
  let host;
  let browser;
  try {
    // --- The host, with this script as Blender ---
    const t0 = Date.now();
    host = spawn(process.execPath, [HOST, '--url', url, '--width', String(WIDTH), '--height', String(HEIGHT), '--mode', MODE, '--max-fps', MAX_FPS, '--parent-pid', String(process.pid), '--devtools'], { stdio: ['ignore', 'pipe', 'pipe'] });
    let hostLog = '';
    host.stderr.on('data', (d) => (hostLog += d));
    const line = await new Promise((resolve, reject) => {
      host.stdout.once('data', (d) => resolve(String(d)));
      host.once('exit', (code) => reject(new Error(`host exited (${code}): ${hostLog}`)));
    });
    report.port_line_ms = Date.now() - t0;
    const { port, token } = JSON.parse(line);
    const socket = net.connect(port, '127.0.0.1');
    await new Promise((r) => socket.once('connect', r));
    socket.setNoDelay(true);
    const send = (cmd) => socket.write(encodeJson('J', cmd));
    send({ type: 'hello', token, formats: ['jpeg'] });
    const reader = new MessageReader();
    const frames = [];
    let status = null;
    /** @type {((f: any) => void)[]} */
    const frameWaiters = [];
    socket.on('data', (chunk) => {
      const now = Date.now();
      for (const { type, payload } of reader.push(chunk)) {
        if (type === 'S') status = JSON.parse(payload.toString('utf8'));
        if (type !== 'F') continue;
        const f = decodeFrame(payload);
        const frame = { ...f, at: now, latency: now - f.timestamp, bytes: f.pixels.length, pixels: Buffer.from(f.pixels) };
        frames.push(frame);
        for (const w of [...frameWaiters]) w(frame);
      }
    });
    const waitStatus = async (pred, ms) => {
      const end = Date.now() + ms;
      while (!(status && pred(status))) {
        if (Date.now() > end) throw new Error(`Timed out; status ${JSON.stringify(status)}\n${hostLog}`);
        await sleep(20);
      }
      return status;
    };
    await waitStatus((s) => s.status === 'ready' && s.devtools, 60_000);
    report.ready_ms = Date.now() - t0;
    report.first_frame_ms = frames.length ? frames[0].at - t0 : null;
    report.browser = status.browser;
    report.renderer = status.renderer;
    report.gpu = status.gpu;
    browser = await puppeteer.connect({ browserWSEndpoint: status.devtools, defaultViewport: null });
    const page = (await browser.pages()).find((p) => p.url().startsWith(url));
    await page.waitForFunction(() => window.__gs?.ready === true, { timeout: 60_000 });
    await sleep(1500);

    // --- Frame rate and latency for SECONDS ---
    const start = frames.length;
    const cpu0 = await cpuSeconds(host.pid);
    const m0 = Date.now();
    await sleep(SECONDS * 1000);
    const cpu1 = await cpuSeconds(host.pid);
    const measured = frames.slice(start);
    const span = (measured.at(-1).at - measured[0].at) / 1000;
    report.fps = +((measured.length - 1) / span).toFixed(1);
    report.page_fps = await page.evaluate(() => new Promise((res) => { let n = 0; const t = performance.now(); const f = () => { n++; if (performance.now() - t < 1000) requestAnimationFrame(f); else res(n); }; requestAnimationFrame(f); }));
    report.frame_kb = +(measured.reduce((s, f) => s + f.bytes, 0) / measured.length / 1024).toFixed(1);
    const latencies = measured.map((f) => f.latency);
    report.frame_latency_ms = { p50: q(latencies, 0.5), p90: q(latencies, 0.9), max: q(latencies, 1) };
    const gaps = measured.slice(1).map((f, i) => f.at - measured[i].at);
    report.frame_gap_ms = { p50: q(gaps, 0.5), p99: q(gaps, 0.99), max: q(gaps, 1) };
    if (cpu0 && cpu1) {
      const wall = (cpu1.at - cpu0.at) / 1000;
      report.cpu_percent_of_one_core = { host: +((100 * (cpu1.host - cpu0.host)) / wall).toFixed(1), browser: +((100 * (cpu1.browser - cpu0.browser)) / wall).toFixed(1), cores: os.cpus().length };
    }
    report.measured_s = +((Date.now() - m0) / 1000).toFixed(1);
    for (const [i, f] of [measured[0], measured[Math.floor(measured.length / 2)], measured.at(-1)].entries()) {
      await writeFile(path.join(OUT, `frame-${i + 1}.jpg`), f.pixels);
    }

    // --- Input round trip: F8 toggles the marker (key -> page -> next frame -> here) ---
    const trips = [];
    for (let i = 0; i < 10; i++) {
      const before = await page.evaluate(() => window.__probe.toggles);
      const sent = Date.now();
      send({ type: 'key', kind: 'down', code: 'F8', key: 'F8' });
      send({ type: 'key', kind: 'up', code: 'F8', key: 'F8' });
      let probe;
      for (;;) {
        probe = await page.evaluate(() => window.__probe);
        if (probe.toggles > before && probe.rafAt) break;
        if (Date.now() - sent > 3000) throw new Error('F8 did not reach the page');
        await sleep(2);
      }
      // The first frame drawn after the marker changed (its draw time is after the rAF that changed it).
      const shown = frames.find((f) => f.at >= sent && f.timestamp >= probe.rafAt) ??
        (await new Promise((resolve) => frameWaiters.push(function w(f) { if (f.timestamp >= probe.rafAt) { frameWaiters.splice(frameWaiters.indexOf(w), 1); resolve(f); } })));
      trips.push({ key: probe.downAt - sent, frame: shown.at - sent });
      await sleep(120);
    }
    // By draw times (the first frame drawn after the page's next rAF), so a lower bound; the Blender run checks pixels.
    report.input_ms = { key_to_page_p50: q(trips.map((t) => t.key), 0.5), key_to_next_frame_here_p50: q(trips.map((t) => t.frame), 0.5), key_to_next_frame_here_max: q(trips.map((t) => t.frame), 1) };

    // --- Game state: hold W, time until the player moves, and until a frame shows it ---
    const moves = [];
    for (let i = 0; i < 5; i++) {
      await page.evaluate(() => {
        const p = window.__gs.world.findByName('Player').object3d;
        const start = p.position.clone();
        window.__move = { start: null, movedAt: 0 };
        const check = () => {
          if (window.__move.start === null) window.__move.start = start;
          if (p.position.distanceTo(start) > 0.01) window.__move.movedAt = Date.now();
          else requestAnimationFrame(check);
        };
        requestAnimationFrame(check);
      });
      const code = i % 2 ? 'KeyS' : 'KeyW';
      const sent = Date.now();
      send({ type: 'key', kind: 'down', code, key: code.slice(3).toLowerCase() });
      let movedAt = 0;
      while (!movedAt && Date.now() - sent < 3000) {
        movedAt = await page.evaluate(() => window.__move.movedAt);
        if (!movedAt) await sleep(2);
      }
      const shown = movedAt ? frames.find((f) => f.timestamp >= movedAt) ?? (await new Promise((resolve) => frameWaiters.push(function w(f) { if (f.timestamp >= movedAt) { frameWaiters.splice(frameWaiters.indexOf(w), 1); resolve(f); } }))) : null;
      send({ type: 'key', kind: 'up', code, key: code.slice(3).toLowerCase() });
      if (movedAt) moves.push({ state: movedAt - sent, frame: shown.at - sent });
      await sleep(400);
    }
    report.game_state_ms = { n: moves.length, key_to_player_moved_p50: q(moves.map((m) => m.state), 0.5), key_to_frame_here_p50: q(moves.map((m) => m.frame), 0.5) };

    // --- Audio: a quiet tone through WebAudio, as the engine plays sound ---
    if (!argv.includes('--no-audio')) {
      send({ type: 'key', kind: 'down', code: 'Space', key: ' ' });
      send({ type: 'key', kind: 'up', code: 'Space', key: ' ' });
      const state = await page.evaluate(() => {
        const c = new AudioContext();
        const o = c.createOscillator();
        const g = c.createGain();
        g.gain.value = 0.03;
        o.frequency.value = 440;
        o.connect(g).connect(c.destination);
        o.start();
        o.stop(c.currentTime + 1.2);
        return c.state;
      });
      await sleep(250);
      let peak = null;
      if (WINDOWS) {
        const ps = path.join(await mkdtemp(path.join(os.tmpdir(), 'gs-probe-')), 'peaks.ps1');
        await writeFile(ps, PEAKS_PS);
        peak = Number((await powershell(['-ExecutionPolicy', 'Bypass', '-File', ps])).trim());
        await rm(path.dirname(ps), { recursive: true, force: true });
      }
      report.audio = { status_says: status.audio, context: state, output_peak: peak, heard: peak === null ? 'unknown' : peak > 0.005 };
    }

    // --- Resize, pause, stop ---
    send({ type: 'size', width: 640, height: 360 });
    const resized = await new Promise((resolve) => {
      const t = setTimeout(() => resolve(null), 3000);
      frameWaiters.push(function w(f) { if (f.width === 640 && f.height === 360) { clearTimeout(t); frameWaiters.splice(frameWaiters.indexOf(w), 1); resolve(f); } });
    });
    report.resize_640x360 = !!resized;
    send({ type: 'pause' });
    await sleep(300);
    const n0 = frames.length;
    await sleep(1000);
    report.paused_frames = frames.length - n0;
    send({ type: 'resume' });
    await sleep(500);
    report.resumed = frames.length > n0;
    await browser.disconnect();
    browser = null;
    const s0 = Date.now();
    send({ type: 'stop' });
    await new Promise((r) => (host.exitCode !== null ? r() : host.once('exit', r)));
    report.stop_ms = Date.now() - s0;
    await sleep(500);
    report.browsers_left = await leftoverBrowsers();
    host = null;
    console.log(JSON.stringify({ node_client: report }, null, 1));

    // --- The same through Blender (core/stream.py) ---
    const blender = findBlender();
    if (!argv.includes('--no-blender') && blender) {
      const profile = await mkdtemp(path.join(os.tmpdir(), 'gs-stream-probe-blender-'));
      try {
        const env = { ...process.env, BLENDER_USER_RESOURCES: profile, TEMP: profile, TMP: profile, TMPDIR: profile };
        const out = await new Promise((resolve, reject) => {
          const proc = spawn(blender, ['--background', '--factory-startup', '--python-exit-code', '1', '--python', path.join(RUNTIME, 'scripts', 'stream_probe_blender.py'), '--', url, PROJECT, OUT, String(SECONDS), String(WIDTH), String(HEIGHT)], { env, stdio: ['ignore', 'pipe', 'pipe'] });
          let stdout = '';
          let stderr = '';
          proc.stdout.on('data', (d) => (stdout += d));
          proc.stderr.on('data', (d) => (stderr += d));
          proc.once('exit', (code) => (code === 0 ? resolve(stdout) : reject(new Error(`Blender exited ${code}: ${stdout.slice(-2000)}${stderr.slice(-2000)}`))));
        });
        const jsonLine = out.split('\n').find((l) => l.startsWith('{'));
        console.log(JSON.stringify({ blender_client: jsonLine ? JSON.parse(jsonLine) : out.slice(-2000) }, null, 1));
        await sleep(500);
        console.log(JSON.stringify({ browsers_left_after_blender: await leftoverBrowsers() }));
      } finally {
        await rm(profile, { recursive: true, force: true }).catch(() => {});
      }
    }
    console.log(`frames saved in ${OUT}`);
  } finally {
    if (browser) await browser.disconnect().catch(() => {});
    if (host && host.exitCode === null) host.kill();
    await server.close();
  }
}

await main();
