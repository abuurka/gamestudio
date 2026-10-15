#!/usr/bin/env node
// @ts-check
// Streams a game page to Blender: plays it in headless Edge/Chrome and sends the frames over TCP;
// Blender sends input back (src/stream-host.js, wire format in src/stream-protocol.js). Started by
// the GameStudio extension (blender/gamestudio/core/stream.py) with the project's Node.js.
//
// Usage: gamestudio-stream [--url URL] [--width 960] [--height 540] [--scale 1] [--quality 80]
//          [--max-fps 60] [--mode headless|offscreen] [--browser PATH] [--parent-pid PID] [--mute]
//          [--devtools]
//
// Prints {"port": N, "token": "..."} as its only stdout line, then waits for Blender to connect;
// logs go to stderr. Exits when Blender disconnects or sends stop, or when --parent-pid exits.
// Without --url the browser starts and waits for Blender's navigate command (Blender starts the
// host while the dev server starts). --devtools adds the browser's DevTools URL to the status
// (probes and tests).

import { DEFAULTS, StreamHost } from '../src/stream-host.js';

const args = process.argv.slice(2);
/** @param {string} name */
const option = (name) => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : undefined;
};
const url = option('--url') ?? '';
if (args.includes('--help') || (url && !/^https?:\/\//i.test(url))) {
  console.error('Usage: gamestudio-stream [--url URL] [--width W] [--height H] [--scale S] [--quality Q] [--max-fps N] [--mode headless|offscreen] [--browser PATH] [--parent-pid PID] [--mute] [--devtools]');
  process.exit(2);
}

/** @param {string} line */
const log = (line) => process.stderr.write(`${new Date().toISOString().slice(11, 23)} ${line}\n`);
const host = new StreamHost({
  url,
  width: Number(option('--width') ?? DEFAULTS.width),
  height: Number(option('--height') ?? DEFAULTS.height),
  scale: Number(option('--scale') ?? DEFAULTS.scale),
  quality: Number(option('--quality') ?? DEFAULTS.quality),
  maxFps: Number(option('--max-fps') ?? DEFAULTS.maxFps),
  mode: option('--mode') ?? process.env.GAMESTUDIO_STREAM_MODE ?? DEFAULTS.mode,
  browserPath: option('--browser') ?? null,
  parentPid: Number(option('--parent-pid') ?? 0),
  mute: args.includes('--mute'),
  exposeDevtools: args.includes('--devtools'),
  log,
});

for (const signal of /** @type {const} */ (['SIGINT', 'SIGTERM', 'SIGHUP'])) {
  process.on(signal, () => void host.shutdown(`Received ${signal}`));
}
process.on('uncaughtException', (error) => {
  log(`uncaught: ${error.stack ?? error}`);
  host.fail(`The stream host failed: ${error.message}`);
});

const { port, token } = await host.listen(Number(option('--port') ?? 0));
process.stdout.write(`${JSON.stringify({ port, token })}\n`);
host.start();
const reason = await host.closed;
log(`exit: ${reason}`);
process.exit(0);
