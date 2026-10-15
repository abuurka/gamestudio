// Where the runtime scripts find a headless browser and Blender: the environment first, then the
// usual install folders on Windows and macOS, then PATH (Linux: google-chrome, chromium, blender).

import { existsSync } from 'node:fs';
import path from 'node:path';
import { findBrowser as findGameBrowser } from '../packages/devserver/src/browser.js';

const WINDOWS = process.platform === 'win32';

/** `names` in every PATH folder, in PATH order. */
function onPath(names) {
  const dirs = (process.env.PATH ?? '').split(path.delimiter).filter(Boolean);
  return dirs.flatMap((dir) => names.map((name) => path.join(dir, WINDOWS ? `${name}.exe` : name)));
}

/**
 * The browser the stream host would use (@gamestudio/devserver src/browser.js): GS_BROWSER or
 * GAMESTUDIO_BROWSER, else Edge, Chrome or Chromium, else the Chrome Headless Shell GameStudio
 * downloaded (GAMESTUDIO_BROWSER_DIR or a Blender profile's GameStudio user folder). Undefined when
 * there is none.
 */
export function findBrowser() {
  const env = process.env.GS_BROWSER ? { ...process.env, GAMESTUDIO_BROWSER: process.env.GS_BROWSER } : process.env;
  return findGameBrowser(env) ?? undefined;
}

/** BLENDER (used as given), else Blender 5.2+ in the usual install folders or on PATH. */
export function findBlender() {
  if (process.env.BLENDER) return process.env.BLENDER;
  const candidates = [];
  if (WINDOWS) {
    for (const drive of ['F', 'C', 'D', 'E']) {
      for (const version of ['5.4', '5.3', '5.2']) {
        candidates.push(`${drive}:\\Program Files\\Blender Foundation\\Blender ${version}\\blender.exe`);
      }
    }
  } else if (process.platform === 'darwin') {
    candidates.push('/Applications/Blender.app/Contents/MacOS/Blender');
  }
  candidates.push(...onPath(['blender']));
  return candidates.find((p) => existsSync(p));
}
