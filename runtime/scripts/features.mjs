// Real run of animation, audio, prefabs, particles, menus and levels: Blender authors and exports
// two levels, a dev server serves them, headless Edge/Chrome plays them (a second page on the test
// clock: start screen, particles, pause menu, a scene change that keeps the score, restart), and
// Blender edits them over the live link.
//
//   node runtime/scripts/features.mjs
//
// Uses a throw-away project in runtime/.smoke/features-project (never examples/demo-game)
// and a scratch Blender profile. Env: BLENDER, GS_BROWSER (default: found by programs.mjs),
// GS_SMOKE_OUT.

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import { findBlender, findBrowser } from './programs.mjs';

const RUNTIME = fileURLToPath(new URL('..', import.meta.url));
const REPO = path.resolve(RUNTIME, '..');
const PROJECT = path.join(RUNTIME, '.smoke', 'features-project');
const OUT = path.resolve(process.env.GS_SMOKE_OUT ?? path.join(RUNTIME, '.smoke'));
const SCENE_SCRIPT = path.join(RUNTIME, 'scripts', 'features_scene.py');
const BLENDER = findBlender();
const BROWSER = findBrowser();

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) throw new Error(`Check failed: ${name} ${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, { timeout = 15000, interval = 100, what = 'condition' } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await sleep(interval);
  }
}

// --- The game project ----------------------------------------------------------

/** 16-bit mono PCM WAV. */
function wav(seconds, sample) {
  const rate = 22050;
  const n = Math.round(seconds * rate);
  const data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, sample(i / rate))) * 32767), i * 2);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVEfmt ', 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

const FILES = {
  'gamestudio.json': JSON.stringify({ name: 'features', formatVersion: 1, startScene: 'main', link: { editorPort: 0, gamePort: 0 } }),
  'vite.config.ts': `import { gamestudio } from '@gamestudio/devserver';
import { defineConfig } from 'vite';

export default defineConfig({ plugins: [gamestudio()] });
`,
  'index.html': `<!doctype html>
<html><head><meta charset="utf-8"><title>features</title><link rel="icon" href="data:,">
<style>html,body{margin:0;height:100%;background:#111}canvas{display:block;width:100%;height:100%}</style></head>
<body><canvas id="game"></canvas><script type="module" src="/src/main.ts"></script></body></html>
`,
  'src/main.ts': `import { createGame } from '@gamestudio/engine';
import.meta.glob('./components/*.ts', { eager: true });
import.meta.glob('./systems/*.ts', { eager: true });
createGame({ canvas: '#game', startScene: 'main' });
`,
  'src/components/spawner.ts': `import { defineComponent, field } from '@gamestudio/engine/schema';

export const Spawner = defineComponent('Spawner', {
  description: 'Spawns prefab instances on a timer, then removes the first one.',
  category: 'Gameplay',
  fields: {
    prefab: field.string('Coin'),
    every: field.float(0.6, { min: 0.05, unit: 'time' }),
    max: field.int(3, { min: 0 }),
    sound: field.asset(['wav', 'ogg', 'mp3']),
  },
});
`,
  'src/systems/flow.ts': `import { defineSystem } from '@gamestudio/engine';

// Only on the ?flow page: a start screen, the pause menu, the score and the way to level2.
const flow = new URLSearchParams(location.search).has('flow');
declare global { interface Window { __flow?: { ticks: number } } }

export default defineSystem({
  name: 'FlowSystem',
  start(ctx) {
    if (!flow) return;
    window.__flow ??= { ticks: 0 };
    ctx.ui.pauseMenu();
    ctx.store.score ??= 0;
    ctx.hud.set('score', 'Score ' + ctx.store.score, { position: 'top-right', size: 'large', color: '#ffd400' });
    ctx.every(0.5, () => window.__flow!.ticks++);
    if (!ctx.store.started && ctx.game.scene.name === 'main') {
      ctx.ui.screen('title', {
        title: 'Feature Island',
        subtitle: 'Particles, menus and levels',
        buttons: [
          { label: 'Play', action: () => { ctx.store.started = true; ctx.ui.close(); } },
          { label: 'Options', disabled: true },
        ],
      });
    }
  },
  update(ctx) {
    if (!flow) return;
    if (ctx.input.keyDown('KeyB')) ctx.vfx.burst(ctx.world.findByName('Flag')!, 'explosion');
    if (ctx.input.keyDown('KeyC')) ctx.vfx.burst([0, 1, 1], 'confetti');
    if (ctx.input.keyDown('KeyN')) {
      ctx.store.score += 5;
      ctx.hud.set('score', 'Score ' + ctx.store.score, { position: 'top-right', size: 'large', color: '#ffd400' });
      void ctx.loadScene('level2', { keep: ['Mascot'] });
    }
  },
});
`,
  'src/systems/spawner.ts': `import { defineSystem, type Entity } from '@gamestudio/engine';
import { Spawner } from '../components/spawner';

interface Probe { spawned: string[]; destroyed: string[]; finished: string[]; loops: number; pops: number; skipped: number; timers: number }
declare global { interface Window { __features?: Probe } }
const probe = (): Probe => (window.__features ??= { spawned: [], destroyed: [], finished: [], loops: 0, pops: 0, skipped: 0, timers: 0 });

export default defineSystem({
  name: 'SpawnerSystem',
  query: [Spawner],
  start() {
    window.__features = undefined;
    probe();
  },
  onAdd(ctx, e) {
    const s = e.get(Spawner);
    const made: Entity[] = [];
    ctx.after(0.25, () => probe().timers++);
    ctx.hud.set('hint', 'Click to hear the scene', { position: 'center', duration: 1.5 });
    const timer = ctx.every(s.every, () => {
      if (made.length >= s.max) {
        timer.cancel();
        probe().destroyed.push(made[0]!.id);
        ctx.destroy(made[0]);
        return;
      }
      const i = made.length;
      const coin = ctx.spawn(s.prefab, { position: [i * 1.2 - 1.2, 0, 1.5], rotation: [0, i * 0.5, 0] });
      made.push(coin);
      probe().spawned.push(coin.id);
      ctx.hud.set('label-' + coin.id, 'Coin ' + made.length, { at: coin, offset: [0, 1, 0], color: '#ffd400', size: 'small' });
      ctx.hud.bar('spawned', made.length, { max: s.max, label: 'Spawned', position: 'top-left' });
      const pop = ctx.audio.play(s.sound, { at: coin, volume: 0.6 });
      if (pop.state === 'stopped') probe().skipped++;
      else probe().pops++;
    });
  },
  update(ctx) {
    for (const event of ctx.animation.events) {
      if (event.type === 'finished') probe().finished.push(event.entity.name + ':' + event.clip);
      else probe().loops++;
    }
  },
});
`,
};

async function writeProject() {
  await rm(PROJECT, { recursive: true, force: true });
  for (const [file, text] of Object.entries(FILES)) {
    await mkdir(path.dirname(path.join(PROJECT, file)), { recursive: true });
    await writeFile(path.join(PROJECT, file), text);
  }
  await mkdir(path.join(PROJECT, 'assets', 'sfx'), { recursive: true });
  // A soft two-tone loop and a short decaying blip.
  await writeFile(path.join(PROJECT, 'assets', 'sfx', 'music.wav'), wav(1, (t) => 0.3 * Math.sin(2 * Math.PI * (t < 0.5 ? 220 : 330) * t)));
  await writeFile(path.join(PROJECT, 'assets', 'sfx', 'pop.wav'), wav(0.2, (t) => 0.5 * Math.exp(-t * 20) * Math.sin(2 * Math.PI * 880 * t)));
}

// --- Blender -------------------------------------------------------------------------

/** Runs features_scene.py; `onEvent` gets each FEATURES line as an object. */
function runBlender(args, onEvent = () => {}) {
  return new Promise((resolve, reject) => {
    mkdtemp(path.join(os.tmpdir(), 'gs_features_profile_')).then((profile) => {
      // A scratch profile: never the user's Blender settings.
      const env = { ...process.env, BLENDER_USER_RESOURCES: profile };
      const proc = spawn(BLENDER, ['--background', '--factory-startup', '--python-exit-code', '1', '--python', SCENE_SCRIPT, '--', ...args], {
        cwd: REPO,
        env,
      });
      const events = [];
      const log = [];
      let carry = '';
      const feed = (chunk) => {
        carry += chunk;
        const lines = carry.split(/\r?\n/);
        carry = lines.pop();
        for (const line of lines) {
          log.push(line);
          if (!line.startsWith('FEATURES ')) continue;
          const event = JSON.parse(line.slice(9));
          events.push(event);
          Promise.resolve(onEvent(event)).catch((error) => {
            proc.kill();
            reject(error);
          });
        }
      };
      proc.stdout.on('data', feed);
      proc.stderr.on('data', feed);
      proc.on('exit', (code) => {
        rm(profile, { recursive: true, force: true }).catch(() => {});
        if (code === 0) resolve(events);
        else reject(new Error(`Blender ${args[0]} exited with ${code}:\n${log.filter((l) => /Error|Traceback|FEATURES|WARNING/.test(l)).slice(-20).join('\n')}`));
      });
    }, reject);
  });
}

// --- The run -----------------------------------------------------------------------

async function main() {
  if (!BLENDER) throw new Error('Blender 5.2+ not found; set BLENDER');
  if (!BROWSER) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');
  await mkdir(OUT, { recursive: true });
  await writeProject();
  const sentinels = await mkdtemp(path.join(os.tmpdir(), 'gs_features_'));

  const server = await createServer({
    root: PROJECT,
    // Its own dependency cache: the throw-away projects share the repo's node_modules, and another
    // project's optimized deps would make Vite re-optimize mid-run (stale "504 Outdated Optimize Dep").
    cacheDir: path.join(PROJECT, '.vite-cache'),
    logLevel: 'warn',
    server: { port: 5192, strictPort: false },
  });
  let browser;
  try {
    await server.listen();
    const url = server.resolvedUrls.local[0];
    console.log(`dev server: ${url} (project ${path.relative(REPO, PROJECT)})`);
    const registry = await waitFor(
      async () => {
        try {
          const data = JSON.parse(await readFile(path.join(PROJECT, '.gamestudio', 'components.json'), 'utf8'));
          const names = data.components.map((c) => c.name);
          return ['Animator', 'AudioSource', 'Spawner'].every((n) => names.includes(n)) ? data : null;
        } catch {
          return null;
        }
      },
      { what: 'components.json', timeout: 30000 },
    );
    check('dev server wrote the registry with the built-in Animator and AudioSource', true, `${registry.components.length} components`);

    // 1. Author and export in Blender.
    const authored = await runBlender(['author', PROJECT]);
    const exported = authored.find((e) => e.kind === 'export');
    const ids = exported.ids;
    const manifest = JSON.parse(await readFile(exported.result.manifest, 'utf8'));
    check(
      'Blender exported the scene (format 2)',
      manifest.formatVersion === 2 && exported.result.prefabs.join() === 'Coin',
      `rev ${manifest.rev}, ${manifest.entities.length} entities, prefabs ${exported.result.prefabs}, ${exported.result.animations} clips`,
    );
    const clipNames = manifest.animations.map((a) => a.name).sort();
    const levels = authored.find((e) => e.kind === 'levels')?.result;
    const level2 = levels?.scenes.find((l) => l.scene === 'level2');
    check(
      'export_all exported both levels; level2.blend (scene "Scene") exports as "level2" from a background Blender',
      levels?.errors.length === 0 && level2?.file === 'scenes/level2.blend' && existsSync(path.join(PROJECT, '.gamestudio', 'export', 'level2.scene.json')),
      JSON.stringify(levels?.scenes.map((l) => [l.file, l.scene, l.rev])),
    );
    const level2Manifest = JSON.parse(await readFile(path.join(PROJECT, '.gamestudio', 'export', 'level2.scene.json'), 'utf8'));
    check(
      'level2 carries its sunset sky, the mist as fog and the PostEffects component',
      level2Manifest.settings.sky?.zenith.length === 3 && level2Manifest.settings.fog?.far === 48 &&
        level2Manifest.entities.some((e) => e.components.PostEffects?.bloom === true),
      JSON.stringify({ sky: level2Manifest.settings.sky, fog: level2Manifest.settings.fog }),
    );
    check(
      'the Fountain ParticleEmitter authored in Blender is in the manifest',
      manifest.entities.find((e) => e.name === 'Fountain')?.components.ParticleEmitter?.rate === 40,
    );
    check('manifest lists the clips, including the NLA strip and the armature action', clipNames.join() === 'Bend,Open,Spin,Wave', clipNames.join());
    check(
      'manifest lists the prefab template with its own clip',
      manifest.prefabs[0].entities.map((e) => e.name).sort().join() === 'CoinBody,CoinRoot' && manifest.prefabs[0].animations[0]?.name === 'CoinBob',
    );
    check(
      'audio files referenced by components were copied into the export',
      Object.keys(manifest.assets).sort().join() === 'assets/sfx/music.wav,assets/sfx/pop.wav' &&
        existsSync(path.join(PROJECT, '.gamestudio', 'export', 'files', 'assets', 'sfx', 'music.wav')),
      JSON.stringify(manifest.assets),
    );

    // 2. Play it.
    browser = await puppeteer.launch({
      executablePath: BROWSER,
      headless: true,
      args: [
        '--use-angle=swiftshader',
        '--enable-unsafe-swiftshader',
        '--ignore-gpu-blocklist',
        '--no-first-run',
        '--window-size=1280,720',
        '--autoplay-policy=user-gesture-required', // as in desktop browsers
      ],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720 });
    const consoleErrors = [];
    const warnings = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
      if (msg.type() === 'warn' || msg.type() === 'warning') warnings.push(msg.text());
    });
    page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`));
    page.on('response', (res) => {
      if (res.status() >= 400) consoleErrors.push(`HTTP ${res.status()} ${res.url()}`);
    });
    await page.goto(`${url}?scene=main`, { waitUntil: 'load' });
    await page.waitForFunction(() => window.__gs?.ready === true && window.__gs.state === 'playing', { timeout: 60000 });

    // Audio before any input: nothing may start, not even an AudioContext.
    const locked = await page.evaluate(() => {
      const audio = window.__gs.game.audio;
      return { unlocked: audio.unlocked, listener: audio.listener !== null, sounds: audio.sounds.map((s) => [s.clip, s.state]) };
    });
    check(
      'before the first input audio stays locked; the autoplay music waits',
      !locked.unlocked && !locked.listener && JSON.stringify(locked.sounds) === '[["assets/sfx/music.wav","pending"]]',
      JSON.stringify(locked),
    );

    // Animation.
    const pose = () =>
      page.evaluate((flagId) => {
        const { world, game } = window.__gs;
        const flag = world.get(flagId);
        const tip = game.three.scene.getObjectByName('Tip');
        return {
          flag: flag.object3d.quaternion.toArray(),
          tip: tip.quaternion.toArray(),
          wave: game.animation.isPlaying(flag, 'Wave'),
          bend: game.animation.isPlaying(world.findByName('Rig'), 'Bend'),
          clips: game.animation.clips().sort(),
        };
      }, ids.Flag);
    const p1 = await pose();
    // Game time, not wall time: with software rendering on a busy machine a frame can take 0.5 s.
    const t1 = await page.evaluate(() => window.__gs.test.status().gameTime);
    await waitFor(() => page.evaluate((t) => window.__gs.test.status().gameTime > t + 0.3, t1), { what: 'game time to pass' });
    const p2 = await pose();
    const moved = (a, b) => a.some((v, i) => Math.abs(v - b[i]) > 1e-3);
    check('Animator plays object actions (Flag waves)', p1.wave && p2.wave && moved(p1.flag, p2.flag), `quat ${p1.flag.map((n) => n.toFixed(3))} → ${p2.flag.map((n) => n.toFixed(3))}`);
    check('Animator plays armature actions on the skinned mesh (Tip bone bends)', p2.bend && moved(p1.tip, p2.tip));
    check('every clip from the scene and prefab GLBs is available', p2.clips.join() === 'Bend,CoinBob,Open,Spin,Wave', p2.clips.join());
    const finished = await waitFor(() => page.evaluate(() => window.__features?.finished.includes('Door:Open')), { what: 'Door:Open to finish' });
    check('a clip played once reports "finished" (Door:Open)', finished);

    // Prefab instance placed in Blender.
    const instance = await page.evaluate((instId) => {
      const { world, game } = window.__gs;
      const part = (name) => world.all().find((e) => e.id.startsWith(`${instId}/`) && e.name === name);
      const root = part('CoinRoot');
      const body = part('CoinBody');
      return root && body && { root: root.id, components: root.components(), bob: game.animation.isPlaying(body, 'CoinBob'), parent: body.parentId };
    }, ids.CoinInstance);
    check(
      'the collection instance of the prefab became entities with the template components',
      instance && instance.components.includes('Spin') && instance.bob && instance.parent === instance.root,
      JSON.stringify(instance),
    );

    // First input: audio starts.
    await page.mouse.click(640, 360);
    await page.waitForFunction(() => window.__gs.game.audio.unlocked, { timeout: 10000 });
    const level = await page.evaluate(async () => {
      const audio = window.__gs.game.audio;
      const context = audio.listener.context;
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      // The audio device takes a moment to start (headless too): wait for the clock to run.
      for (let i = 0; i < 50 && context.currentTime < 0.1; i++) await sleep(100);
      const analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      audio.listener.gain.connect(analyser);
      const t0 = context.currentTime;
      await sleep(400);
      const data = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(data);
      const rms = Math.sqrt(data.reduce((s, v) => s + v * v, 0) / data.length);
      audio.listener.gain.disconnect(analyser);
      return {
        state: context.state,
        advanced: context.currentTime - t0,
        rms,
        music: audio.sounds.find((s) => s.clip === 'assets/sfx/music.wav')?.state,
        listenerOnCamera: audio.listener.parent === window.__gs.game.three.camera,
      };
    });
    check(
      'after a click the AudioContext runs and the looping music is audible at the camera listener',
      level.state === 'running' && level.music === 'playing' && level.rms > 1e-3 && level.listenerOnCamera,
      `rms ${level.rms.toFixed(4)}, clock advanced ${level.advanced.toFixed(2)} s`,
    );

    // Spawning from code.
    const spawn = await waitFor(
      () => page.evaluate(() => (window.__features?.destroyed.length ? window.__features : null)),
      { what: 'three spawns and one destroy', timeout: 20000 },
    );
    const spawned = await page.evaluate((list) => {
      const { world, game } = window.__gs;
      return list.map((id) => {
        const e = world.get(id);
        const body = e && world.all().find((c) => c.parentId === id && c.name === 'CoinBody');
        return e ? { id, name: e.name, spin: e.get('Spin').speed, bob: game.animation.isPlaying(body, 'CoinBob'), pos: e.object3d.position.toArray() } : { id, gone: true, childGone: !world.all().some((c) => c.parentId === id) };
      });
    }, spawn.spawned);
    check(
      'ctx.spawn("Coin") creates prefab instances with components and playing clips',
      spawned.length === 3 && spawned.slice(1).every((s) => s.name === 'CoinRoot' && s.spin === 2 && s.bob && /^rt_\d+$/.test(s.id)),
      JSON.stringify(spawned.slice(1).map((s) => [s.id, s.pos.map((n) => +n.toFixed(2))])),
    );
    check('ctx.destroy removes an instance with its children', spawned[0].gone && spawned[0].childGone, spawned[0].id);
    check('ctx.after and ctx.every fired on game time', spawn.timers === 1);
    const hud = await page.evaluate((ids) => {
      const element = (key) => document.querySelector(`.gs-hud [data-key="${key}"]`);
      const labels = ids.map((id) => element(`label-${id}`));
      const live = labels[1];
      return {
        bar: element('spawned')?.dataset.value,
        removedWithEntity: labels[0] === null,
        text: live?.textContent,
        color: live?.style.color,
        pinned: live && live.style.display !== 'none' && /translate\(/.test(live.style.transform),
        hint: element('hint') !== null,
      };
    }, spawn.spawned);
    check(
      'HUD: labels pinned to spawned coins (removed with their entity), a full bar, and an expired timed message',
      hud.bar === '1' && hud.removedWithEntity && hud.text === 'Coin 2' && hud.pinned && hud.color !== '' && !hud.hint,
      JSON.stringify(hud),
    );
    check('one-shot sounds play after the first input (and are skipped before it)', spawn.pops >= 1, `${spawn.pops} played, ${spawn.skipped} skipped`);
    await page.screenshot({ path: path.join(OUT, 'features-playing.png') });
    const fountainLive = await page.evaluate(() => window.__gs.game.vfx.count(window.__gs.world.findByName('Fountain')));
    check('the Blender-authored Fountain emits in the real-time game', fountainLive > 10, `${fountainLive} particles`);

    // 3. Live link: Blender edits the Animator and adds a prefab instance.
    const before = await page.evaluate(() => window.__gs.game.scene.rev);
    await runBlender(['live', PROJECT, sentinels], async (event) => {
      if (event.kind === 'live-edit') {
        const current = await waitFor(
          () => page.evaluate((flagId) => {
            const { world, game } = window.__gs;
            const flag = world.get(flagId);
            return game.animation.current(flag) === 'Spin' && game.animation.isPlaying(flag, 'Spin') ? 'Spin' : null;
          }, ids.Flag),
          { what: 'the Flag to switch to Spin' },
        );
        check('editing Animator.clip in Blender switches the clip in the running game', event.clip === 'Spin' && current === 'Spin', `game state clip ${event.clip}`);
        await writeFile(path.join(sentinels, 'live-clip.ok'), 'ok');
      } else if (event.kind === 'reexport') {
        const reloaded = (instId) => {
          const { world, game } = window.__gs;
          const flag = world.findByName('Flag');
          return {
            rev: game.scene.rev,
            frame: game.ready && game.state === 'playing' && flag ? game.animation.current(flag) !== null : false,
            parts: world.all().filter((e) => e.id.startsWith(instId + '/')).map((e) => e.name).sort(),
            flag: flag ? game.animation.current(flag) : null,
            playing: flag ? game.animation.isPlaying(flag) : false,
          };
        };
        let state = null;
        // The new scene is built first; its Animators start on its first simulated frame.
        await waitFor(
          async () => {
            state = await page.evaluate(reloaded, event.instance);
            return state.rev > before && state.frame;
          },
          { what: 'the re-exported scene to load and play' },
        ).catch((error) => {
          throw new Error(`${error.message}: ${JSON.stringify(state)}`);
        });
        check(
          'a live re-export reloads the game with the new prefab instance and the edited Animator',
          state.rev === event.rev && state.parts.join() === 'CoinBody,CoinRoot' && state.flag === 'Spin' && state.playing,
          `rev ${before} → ${state.rev}, ${JSON.stringify({ parts: state.parts, flag: state.flag, playing: state.playing })}`,
        );
        await writeFile(path.join(sentinels, 'reexport.ok'), 'ok');
      } else if (event.kind === 'error') {
        throw new Error(`Blender: ${event.message}`);
      }
    });
    await sleep(300);
    await page.screenshot({ path: path.join(OUT, 'features-live.png') });

    // 4. The flow page: the test clock drives a start screen, particles, the pause menu and a level change.
    const flowPage = await browser.newPage();
    await flowPage.setViewport({ width: 960, height: 540 });
    const flowErrors = [];
    flowPage.on('console', (msg) => {
      if (msg.type() === 'error' || ((msg.type() === 'warn' || msg.type() === 'warning') && msg.text().includes('[gamestudio]'))) flowErrors.push(msg.text());
    });
    flowPage.on('pageerror', (error) => flowErrors.push(`pageerror: ${error.message}`));
    await flowPage.evaluateOnNewDocument(() => {
      window.__gsTestHold = true;
    });
    await flowPage.goto(`${url}?scene=main&flow=1`, { waitUntil: 'load' });
    await flowPage.waitForFunction(() => window.__gs?.ready === true, { timeout: 60000 });
    const T = {
      begin: () => flowPage.evaluate(() => window.__gs.test.begin()),
      advance: (n) => flowPage.evaluate((k) => window.__gs.test.advance(k), n),
      schedule: (inputs) => flowPage.evaluate((i) => window.__gs.test.schedule(i), inputs),
      status: () => flowPage.evaluate(() => window.__gs.test.status()),
      state: () => flowPage.evaluate(() => window.__gs.test.state({ limit: 0 })),
      shot: async (name) => {
        await flowPage.evaluate(() => window.__gs.test.render());
        await flowPage.screenshot({ path: path.join(OUT, name) });
      },
      fountain: () => flowPage.evaluate(() => window.__gs.game.vfx.count(window.__gs.world.findByName('Fountain'))),
      step: async () => (await flowPage.evaluate(() => window.__gs.test.status())).step,
    };
    await T.begin();
    await T.advance(3);
    let status = await T.status();
    let flowState = await T.state();
    check(
      'the start screen shows over the paused game, with its buttons',
      status.screen === 'title' && status.state === 'paused' && flowState.ui.focus === 'Play' &&
        flowState.ui.buttons.map((b) => `${b.label}${b.disabled ? '(off)' : ''}`).join() === 'Play,Options(off)',
      JSON.stringify(flowState.ui.buttons.map((b) => [b.label, b.x, b.y])),
    );
    await T.shot('features-title.png');
    await flowPage.evaluate(() => window.__gs.game.vfx.emit(window.__gs.world.findByName('Fountain'), 12));
    const frozen = await flowPage.evaluate(() => window.__gs.test.state({ limit: 0 }).vfx);
    await T.advance(90); // longer than a particle lives
    const still = await flowPage.evaluate(() => window.__gs.test.state({ limit: 0 }).vfx);
    check(
      'nothing moves under the start screen: game time and particles wait too',
      frozen.particles === 12 && still.particles === 12 && (await T.status()).gameTime === status.gameTime,
      `${still.particles} particles after 1.5 s`,
    );
    let at = await T.step();
    await T.schedule([{ step: at + 1, key: 'Enter' }]);
    await T.advance(3);
    status = await T.status();
    check('Enter on Play closes the start screen and play resumes', status.screen === null && status.state === 'playing', JSON.stringify({ screen: status.screen, state: status.state }));
    await T.advance(60);
    const fountain = await T.fountain();
    check('the Blender-authored emitter streams particles under the test clock', fountain > 20, `${fountain} particles`);
    at = await T.step();
    await T.schedule([{ step: at + 1, key: 'KeyB' }, { step: at + 1, key: 'KeyC' }]);
    await T.advance(12);
    flowState = await T.state();
    check('ctx.vfx.burst plays the explosion and confetti presets', flowState.vfx.particles > fountain + 100, JSON.stringify(flowState.vfx));
    await T.shot('features-vfx.png');

    at = await T.step();
    await T.schedule([{ step: at + 1, key: 'Escape' }]);
    await T.advance(3);
    status = await T.status();
    const ticks = await flowPage.evaluate(() => window.__flow.ticks);
    await T.advance(40);
    const paused = await T.status();
    check(
      'Escape opens the pause menu: game time, timers and particles stop',
      status.screen === 'pause' && status.state === 'paused' && paused.gameTime === status.gameTime &&
        (await flowPage.evaluate(() => window.__flow.ticks)) === ticks,
      JSON.stringify({ screen: status.screen, gameTime: [status.gameTime, paused.gameTime], ticks }),
    );
    await T.shot('features-pause.png');
    at = await T.step();
    await T.schedule([{ step: at + 1, key: 'KeyP' }]);
    await T.advance(3);
    check('P closes the pause menu and play resumes', (await T.status()).state === 'playing');

    const mascotId = ids.Mascot;
    at = await T.step();
    await T.schedule([{ step: at + 1, key: 'KeyN' }]);
    await T.advance(70);
    status = await T.status();
    const inLevel2 = await flowPage.evaluate((id) => {
      const { game, world } = window.__gs;
      const mascot = world.get(id);
      return {
        score: game.store.score,
        mascot: mascot ? mascot.object3d.getWorldPosition(mascot.object3d.position.clone()).toArray().map((n) => Math.round(n * 100) / 100) : null,
        mascots: world.all().filter((e) => e.name === 'Mascot').length,
        portal: game.vfx.count(world.findByName('Portal')),
        sky: game.three.scene.background?.isDataTexture === true,
        fog: game.three.scene.fog ? [game.three.scene.fog.near, game.three.scene.fog.far] : null,
        bloom: game.post.settings.bloom,
        hud: document.querySelector('.gs-hud [data-key="score"]')?.textContent,
      };
    }, mascotId);
    check(
      'ctx.loadScene("level2") fades over to level 2 and the playtest status reports it (a game scene change, not a reload)',
      status.scene.name === 'level2' && status.sceneChanges === 1 && status.loads === 0 && status.state === 'playing',
      JSON.stringify({ scene: status.scene, sceneChanges: status.sceneChanges, loads: status.loads }),
    );
    check(
      'the score survives the scene change (ctx.store), and the kept Mascot takes the place of level 2\'s own',
      inLevel2.score === 5 && inLevel2.hud === 'Score 5' && inLevel2.mascots === 1 && inLevel2.mascot && Math.abs(inLevel2.mascot[0] + 2) < 0.01 && Math.abs(inLevel2.mascot[2] - 1.5) < 0.01,
      JSON.stringify(inLevel2),
    );
    check(
      'level 2 draws its sky gradient, fog, bloom and its Portal particles',
      inLevel2.sky && inLevel2.fog?.join() === '8,48' && inLevel2.bloom && inLevel2.portal > 20,
      JSON.stringify({ fog: inLevel2.fog, portal: inLevel2.portal }),
    );
    await T.shot('features-level2.png');

    // The live link still works after the scene change: Blender opens level2 and moves the Portal.
    const portalBefore = (await flowPage.evaluate(() => window.__gs.test.positions(['Portal']))).Portal;
    await runBlender(['levels', PROJECT, sentinels], async (event) => {
      if (event.kind === 'moved') {
        const moved = await waitFor(
          async () => {
            const now = (await flowPage.evaluate(() => window.__gs.test.positions(['Portal']))).Portal;
            return Math.abs(now[0] - portalBefore[0] - 1.5) < 0.01 ? now : null;
          },
          { what: 'the Portal to move in level 2' },
        );
        check('a live edit in level2.blend reaches the game after the scene change', event.scene === 'level2' && !!moved, `${portalBefore} → ${moved}`);
        await writeFile(path.join(sentinels, 'moved.ok'), 'ok');
      } else if (event.kind === 'error') {
        throw new Error(`Blender: ${event.message}`);
      }
    });

    at = await T.step();
    await T.schedule([{ step: at + 1, key: 'Escape' }, { step: at + 3, key: 'ArrowDown' }, { step: at + 5, key: 'Enter' }]);
    await T.advance(80);
    status = await T.status();
    const restarted = await flowPage.evaluate(() => ({ score: window.__gs.game.store.score, mascots: window.__gs.world.all().filter((e) => e.name === 'Mascot').length }));
    check(
      'Restart in the pause menu goes back to the start scene with an empty store: the start screen again',
      status.scene.name === 'main' && status.screen === 'title' && restarted.score === 0 && status.sceneChanges === 2,
      JSON.stringify({ scene: status.scene.name, screen: status.screen, ...restarted }),
    );
    await flowPage.evaluate(() => window.__gs.test.end());
    check('no errors or engine warnings on the flow page', flowErrors.length === 0, flowErrors.join(' | '));
    await flowPage.close();
    console.log(`screenshots in ${OUT}: features-playing.png, features-live.png, features-title.png, features-vfx.png, features-pause.png, features-level2.png`);

    const engineWarnings = warnings.filter((w) => w.includes('[gamestudio]'));
    check('no engine warnings', engineWarnings.length === 0, engineWarnings.join(' | '));
    check('no console errors', consoleErrors.length === 0, consoleErrors.join(' | '));
  } finally {
    await browser?.close();
    await server.close();
    await rm(sentinels, { recursive: true, force: true });
  }
  console.log(`\nfeatures: ${results.filter((r) => r.ok).length}/${results.length} checks passed`);
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(`\nfeatures FAILED: ${error instanceof Error ? error.stack : error}`);
    process.exit(1);
  },
);
