#!/usr/bin/env node
// @ts-check
// The browser GameStudio downloads when the machine has no Edge, Chrome or Chromium: Chrome Headless
// Shell, a pinned build (src/browser-install.js). The GameStudio extension runs it with its Node.js
// as a step of its first-run setup; the stream host and the MCP server find the result
// (src/browser.js).
//
// Usage: gamestudio-browser install [--dir FOLDER]
//        gamestudio-browser path [--dir FOLDER]
// FOLDER defaults to $GAMESTUDIO_BROWSER_DIR. Every stdout line is one JSON object:
//   install  {"stage": "download", "total": N, "url": ...}, {"progress": 0..1, "bytes": N, "total": N}...,
//            {"stage": "extract"}, {"stage": "verify"}, then {"path": EXECUTABLE, "version", "buildId",
//            "downloaded": false when it was there already}; or {"error": SENTENCE, "offline": true?}
//            and exit code 1. A second install into the same folder waits ({"stage": "waiting"}).
//   path     {"path": EXECUTABLE or null, "current": is it the pinned build, "buildId": the pinned
//            build, "platform", "downloadBytes": size of the download for this machine}
// Exit code 2: bad usage.

import { runCli } from '../src/browser-install.js';

const code = await runCli(process.argv.slice(2));
// Exits once stdout is flushed (pipes are asynchronous on macOS); a stalled download would keep it alive.
process.stdout.write('', () => process.exit(code));
