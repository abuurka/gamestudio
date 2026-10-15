#!/usr/bin/env node
// Captures game shaders from the shader gallery in headless Edge/Chrome on the GPU (ANGLE D3D11 on
// Windows; SwiftShader when there is no GPU, or with --swiftshader). Prints per shader: compile
// errors, warnings, page console errors and warnings, and the frame time against the plain material.
// Exit code 1 when any shader has a compile error or its apply/mesh failed; 2 on a usage or setup error.
//
//   node tools/shader-gallery/capture.mjs <Shader...|--all> [options]
//
//   --mesh m          sphere | box | plane | cylinder | character | tree | grass | terrain | flag | building | gem
//                     (every mesh has UVs as a GLB from Blender: V flipped; main.ts describes each)
//   --t seconds       game time of the picture
//   --values json     field values over the shader's preview values, e.g. '{"speed":2}'
//   --camera c        near | far | top | eye (standing on the ground at 1.7 m: big surfaces at game scale)
//   --scale n         scales the preview mesh and its framing (--mesh plane --scale 25: a 200 m sea)
//   --color r,g,b[,a] the preview material's base colour, linear (a < 1: its opacity)
//   --transparent     Alpha Blend like a GLB from Blender (transparent, no depth writes)
//   --opacity a       the material's opacity, 0..1
//   --fog near,far[,r,g,b]  linear distance fog in metres (colour: the sky's horizon)
//   --sky s           day (default) | sunset | night | overcast | none (a flat background)
//   --occluder        a wall between the camera and the left half of the subject (x-ray, hidden outlines)
//   --bloom           the engine's bloom
//   --size WxH        picture size (default 640x480)
//   --out dir         where the PNGs go (default .dev/tmp/shader-gallery/; never C:)
//   --sheet           also a contact sheet of every capture (sheet.png)
//   --swiftshader     software rendering
//
// Without options a shader is drawn as its `preview` says (mesh, camera, t, values, color, transparent,
// scale, occluder); the stage is the same for every shader (sky, sun, fill, ground). PNGs go to
// --out, one per shader (<Shader>.png). Asset paths in values resolve against tools/shader-gallery/public/
// (e.g. samples/...). The same options work as URL parameters of the gallery page (see main.ts).

import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { findBrowser } from '../../runtime/packages/devserver/src/stream-host.js';

const here = fileURLToPath(new URL('.', import.meta.url));
const repo = path.resolve(here, '../..');
const scratch = path.join(repo, '.dev', 'tmp', 'shader-gallery');

function usage(message) {
  if (message) console.error(message);
  console.error(
    'usage: node tools/shader-gallery/capture.mjs <Shader...|--all> [--mesh m] [--t s] [--values json] [--camera c] [--scale n] [--color r,g,b[,a]]\n' +
      '         [--transparent] [--opacity a] [--fog near,far[,r,g,b]] [--sky s] [--occluder] [--bloom] [--size WxH] [--out dir] [--sheet] [--swiftshader]',
  );
  process.exit(2);
}

function parseArgs(argv) {
  const options = { shaders: [], all: false, sheet: false, bloom: false, swiftshader: false, out: scratch, size: [640, 480] };
  const list = (arg, value, lengths) => {
    const numbers = value.split(',').map(Number);
    if (!lengths.includes(numbers.length) || !numbers.every(Number.isFinite)) usage(`${arg} takes ${lengths.join(' or ')} comma-separated numbers`);
    return numbers;
  };
  const positive = (arg, value) => {
    const n = Number(value);
    if (!(n > 0)) usage(`${arg} takes a positive number`);
    return n;
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) usage(`${arg} needs a value`);
      return argv[++i];
    };
    if (arg === '--all') options.all = true;
    else if (arg === '--sheet') options.sheet = true;
    else if (arg === '--bloom') options.bloom = true;
    else if (arg === '--swiftshader') options.swiftshader = true;
    else if (arg === '--transparent') options.transparent = true;
    else if (arg === '--occluder') options.occluder = true;
    else if (arg === '--scale') options.scale = positive(arg, next());
    else if (arg === '--opacity') {
      options.opacity = Number(next());
      if (!(options.opacity >= 0 && options.opacity <= 1)) usage('--opacity is a number from 0 to 1');
    } else if (arg === '--fog') options.fog = list(arg, next(), [2, 5]);
    else if (arg === '--sky') options.sky = next();
    else if (arg === '--mesh') options.mesh = next();
    else if (arg === '--camera') options.camera = next();
    else if (arg === '--t') options.t = Number(next());
    else if (arg === '--out') options.out = path.resolve(next());
    else if (arg === '--color') options.color = list(arg, next(), [3, 4]);
    else if (arg === '--size') {
      const [w, h] = next().split('x').map(Number);
      if (!(w > 0 && h > 0)) usage('--size is WIDTHxHEIGHT, e.g. 640x480');
      options.size = [w, h];
    } else if (arg === '--values') {
      try {
        options.values = JSON.parse(next());
      } catch (error) {
        usage(`--values is not JSON: ${error.message}`);
      }
    } else if (arg === '--help' || arg === '-h') usage();
    else if (arg.startsWith('--')) usage(`unknown option ${arg}`);
    else options.shaders.push(arg);
  }
  if (!options.all && !options.shaders.length) usage('name a shader, or --all');
  if (/^[a-z]:[\\/]/i.test(options.out) && options.out[0].toLowerCase() === 'c') usage('--out must not be on C:');
  return options;
}

const options = parseArgs(process.argv.slice(2));
const browserPath = findBrowser();
if (!browserPath) usage('No Edge or Chrome found (set GAMESTUDIO_BROWSER to its executable)');

const { createServer } = await import('vite');
const puppeteer = (await import('puppeteer-core')).default;

mkdirSync(options.out, { recursive: true });
const profile = path.join(scratch, `profile-${process.pid}`);
mkdirSync(profile, { recursive: true });

// No file watching and no HMR: an edit elsewhere (another agent saving a shader) must not reload the
// page in the middle of a run. A run captures the sources as they were when it started.
const server = await createServer({
  configFile: path.join(here, 'vite.config.mjs'),
  server: { host: '127.0.0.1', port: 0, strictPort: false, hmr: false, watch: null },
});
await server.listen();
const address = server.httpServer.address();
const url = `http://127.0.0.1:${address.port}/`;

async function launch(software) {
  const args = ['--no-first-run', '--no-default-browser-check', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader', '--disable-background-timer-throttling'];
  if (software) args.push('--use-angle=swiftshader');
  else if (process.platform === 'win32') args.push('--use-angle=d3d11');
  const browser = await puppeteer.launch({ executablePath: browserPath, headless: true, userDataDir: profile, args, protocolTimeout: 120_000 });
  const page = await browser.newPage();
  await page.setViewport({ width: options.size[0], height: options.size[1] });
  const console_ = [];
  page.on('console', (m) => {
    if (m.type() !== 'error' && m.type() !== 'warn' && m.type() !== 'warning') return;
    const text = m.text();
    // D3D's HLSL compiler notes about constant folding in three's own packing code (every program
    // with shadows has them): not about the shader.
    const notes = text.split('\n').slice(1).filter((l) => l.trim());
    if (/Program Info Log/.test(text) && /warning X4122/.test(text) && notes.every((l) => /warning X4122/.test(l))) return;
    // Said with its URL below.
    if (/^Failed to load resource: the server responded with a status of/.test(text)) return;
    console_.push(`${m.type()}: ${text}`);
  });
  page.on('pageerror', (e) => console_.push(`pageerror: ${e?.message ?? e}`));
  page.on('response', (r) => {
    // The browser's own favicon request is not the page's.
    if (r.status() >= 400 && !/\/favicon\.ico$/.test(r.url())) console_.push(`error: ${r.status()} ${r.url()}`);
  });
  try {
    await open(page);
  } catch (error) {
    await browser.close();
    throw new Error(`The gallery page failed (${software ? 'SwiftShader' : 'GPU'}): ${error.message}`);
  }
  const info = await page.evaluate(() => window.__gallery.info());
  return { browser, page, console: console_, info };
}

/** Loads the gallery page and waits for `window.__gallery`. */
async function open(page) {
  await page.goto(url, { waitUntil: 'load', timeout: 120_000 });
  await page.waitForFunction(() => !!window.__gallery || !!window.__galleryError, { timeout: 120_000 });
  const failure = await page.evaluate(() => window.__galleryError ?? null);
  if (failure) throw new Error(failure);
}

/** One capture; reopens the page and tries once more when the page went away (a crash or a lost context). */
async function capture(page, request) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await page.evaluate((o) => window.__gallery.render(o), request);
    } catch (error) {
      const message = String(error?.message ?? error);
      if (attempt > 0 || !/context was destroyed|reading 'render'|Target closed|detached/i.test(message)) throw error;
      await open(page);
    }
  }
}

let exitCode = 0;
let session;
try {
  try {
    session = await launch(options.swiftshader);
  } catch (error) {
    if (options.swiftshader) throw error;
    console.log(`${error.message}; trying SwiftShader`);
    session = await launch(true); // no usable GPU (VMs, CI)
  }
  console.log(`GPU: ${session.info.gpu}${/swiftshader|software|llvmpipe/i.test(session.info.gpu) ? ' (software: frame times are not representative)' : ''}`);
  const listed = await session.page.evaluate(() => window.__gallery.list());
  const names = options.all ? listed.map((s) => s.name) : options.shaders;
  for (const name of names) if (!listed.some((s) => s.name === name)) usage(`No game shader "${name}" (known: ${listed.map((s) => s.name).join(', ')})`);
  const { meshes, cameras, skies } = session.info;
  if (options.mesh && !meshes.includes(options.mesh)) usage(`--mesh is one of ${meshes.join(', ')}`);
  if (options.camera && !cameras.includes(options.camera)) usage(`--camera is one of ${cameras.join(', ')}`);
  if (options.sky && !skies.includes(options.sky)) usage(`--sky is one of ${skies.join(', ')}`);

  const captures = [];
  for (const name of names) {
    session.console.length = 0;
    const request = {
      shader: name,
      mesh: options.mesh,
      t: options.t,
      values: options.values,
      camera: options.camera,
      bloom: options.bloom,
      color: options.color,
      scale: options.scale,
      transparent: options.transparent,
      opacity: options.opacity,
      fog: options.fog,
      sky: options.sky,
      occluder: options.occluder,
      width: options.size[0],
      height: options.size[1],
    };
    let result;
    try {
      result = await capture(session.page, request);
    } catch (error) {
      result = { shader: name, errors: [String(error?.message ?? error)], compileErrors: [], warnings: [], png: '', drawMs: 0, baselineMs: 0 };
    }
    const file = path.join(options.out, `${name}.png`);
    if (result.png) writeFileSync(file, Buffer.from(result.png.split(',')[1], 'base64'));
    const failed = result.warnings.filter((w) => w.includes(' failed on material '));
    const broken = result.compileErrors.length > 0 || failed.length > 0 || result.errors.length > 0;
    if (broken) exitCode = 1;
    const pageLines = session.console.filter((line) => !result.warnings.some((w) => line.includes(w)));
    const timing = result.drawMs ? `${result.drawMs.toFixed(2)} ms/frame (plain ${result.baselineMs.toFixed(2)})` : '';
    console.log(`${broken ? 'FAIL' : 'ok  '} ${name.padEnd(22)} ${(result.mesh ?? '').padEnd(9)} t=${result.t ?? '?'}  ${timing}  ${result.png ? path.relative(process.cwd(), file) : '(no image)'}`);
    for (const error of result.compileErrors) console.log(`  compile error:\n${error.replace(/^/gm, '    ')}`);
    for (const error of result.errors) console.log(`  error: ${error.split('\n').slice(0, 4).join('\n    ')}`);
    for (const warning of result.warnings) console.log(`  warning: ${warning}`);
    for (const line of pageLines) console.log(`  console ${line.slice(0, 400)}`);
    captures.push({ name, png: result.png, ok: !broken, note: result.drawMs ? `${result.drawMs.toFixed(1)} ms` : '' });
  }
  if (options.sheet) {
    const png = await session.page.evaluate((items) => window.__gallery.sheet(items, { columns: 6, cell: [320, 240] }), captures);
    const file = path.join(options.out, 'sheet.png');
    writeFileSync(file, Buffer.from(png.split(',')[1], 'base64'));
    console.log(`sheet: ${path.relative(process.cwd(), file)}`);
  }
} catch (error) {
  console.error(error?.stack ?? String(error));
  exitCode = 2;
} finally {
  await session?.browser.close().catch(() => {});
  await server.close();
  if (existsSync(profile)) rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
process.exit(exitCode);
