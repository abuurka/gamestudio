// @ts-check
// SPDX-License-Identifier: MIT
// The third-party notices of a build: THIRD-PARTY-NOTICES.md at the root of the output, a plain file
// next to index.html that ships wherever the game does. Vite's `build.license` writes it with every
// package from node_modules in the bundle and that package's licence file. collectNotices() finds
// what Vite leaves out, and completeNotices() adds it to the file:
// - packages linked from outside node_modules (a project that links @gamestudio/engine with `file:`,
//   or a workspace): Vite only looks inside node_modules;
// - the notices files bundled packages keep next to their licence (the engine's THIRD-PARTY-NOTICES.md,
//   for the shader code it borrows; Apache NOTICE files);
// - files inside a package under another author's licence (three's meshopt decoder);
// - the licences of the fonts in the scene exports (Game UI fonts: Inter.woff2 has Inter-OFL.txt).

import { readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

/** The notices file at the root of a build's output. */
export const NOTICES_FILE = 'THIRD-PARTY-NOTICES.md';
/** Where Vite writes its licence file for `build.license: true`. */
export const VITE_LICENSE_FILE = '.vite/license.md';

const require = createRequire(import.meta.url);
// The same tests as Vite's licence plugin.
const IN_NODE_MODULES = /(?:^|[\\/])node_modules(?:[\\/]|$)/;
const LICENSE_FILES = [/^license/i, /^licence/i, /^copying/i];
const NOTICE_FILES = /^(third[-_]?party[-_]?notices|notice)(\.(md|txt))?$/i;
const FONT_EXTENSIONS = new Set(['.ttf', '.otf', '.ttc', '.woff', '.woff2']);
// A font's licence, next to it: <font name>-OFL.txt as GameStudio writes it, else a shared one
// (the names the Blender export looks for, core/gameui_export.py FONT_LICENCE_NAMES).
const FONT_LICENSES = (/** @type {string} */ stem) => [
  `${stem}-OFL.txt`,
  `${stem}-LICENSE.txt`,
  `${stem}-LICENCE.txt`,
  'OFL.txt',
  'LICENSE.txt',
];
// Vite's text for a bundle without dependencies, and its usual opening.
const VITE_EMPTY = 'The app does not bundle any dependencies with licenses.';
const VITE_INTRO = 'The app bundles dependencies which contain the following licenses:';

/**
 * Files inside bundled packages that are under another licence than the package's own.
 * @type {{ file: RegExp, name: string, identifier: string, license: () => string }[]}
 */
const EMBEDDED = [
  {
    file: /[\\/]three[\\/]examples[\\/]jsm[\\/]libs[\\/]meshopt_decoder\.module\.js$/,
    name: 'meshoptimizer (MeshoptDecoder in three/examples/jsm/libs/meshopt_decoder.module.js)',
    identifier: 'MIT',
    // The decoder is built from meshoptimizer, which the compression step (compress.js) uses too.
    license: () => path.join(path.dirname(require.resolve('meshoptimizer')), 'LICENSE.md'),
  },
];

/** @typedef {{ name: string, version?: string, identifier?: string, text?: string }} Notice */
/** @typedef {{ dir: string, data: Record<string, unknown> }} PackageData */

/**
 * The notices a build needs besides Vite's list of node_modules packages.
 * @param {string} root Vite root (absolute): the game's own package is left out.
 * @param {Iterable<string>} moduleIds every module in the bundle (its chunks' `moduleIds`)
 * @param {string} exportDir `.gamestudio/export`, which the build copies to `__gs/export/`
 * @returns {{ notices: Notice[], unlicensedFonts: string[] }} the fonts without a licence file, as build paths
 */
export function collectNotices(root, moduleIds, exportDir) {
  /** @type {Map<string, PackageData | null>} */
  const cache = new Map();
  /** @type {Map<string, { pkg: PackageData, linked: boolean }>} package dir -> what the bundle has of it */
  const packages = new Map();
  /** @type {Set<(typeof EMBEDDED)[number]>} */
  const embedded = new Set();
  const own = path.resolve(root);
  for (const id of moduleIds) {
    if (id.startsWith('\0') || !path.isAbsolute(id)) continue;
    for (const entry of EMBEDDED) if (entry.file.test(id)) embedded.add(entry);
    const pkg = mainPackage(path.dirname(id), cache);
    if (!pkg || packages.has(pkg.dir)) continue;
    const linked = !IN_NODE_MODULES.test(id);
    // The game's own modules (and a workspace root above it) are not third-party.
    if (linked && inside(own, pkg.dir)) continue;
    packages.set(pkg.dir, { pkg, linked });
  }

  /** @type {Notice[]} */
  const notices = [];
  const sorted = [...packages.values()].sort((a, b) => String(a.pkg.data.name).localeCompare(String(b.pkg.data.name)));
  // Linked packages, as Vite lists the others.
  for (const { pkg } of sorted.filter((p) => p.linked)) {
    const { name, version = '0.0.0', license } = /** @type {{ name: string, version?: string, license?: unknown }} */ (pkg.data);
    /** @type {Notice} */
    const notice = { name, version };
    if (typeof license === 'string') notice.identifier = license.trim();
    const file = findFile(pkg.dir, (f) => LICENSE_FILES.some((re) => re.test(f)));
    if (file) notice.text = readText(file);
    notices.push(notice);
  }
  // Their notices files: the code they contain from other authors.
  for (const { pkg } of sorted) {
    const file = findFile(pkg.dir, (f) => NOTICE_FILES.test(f));
    if (!file) continue;
    const name = `Code from other authors in ${String(pkg.data.name)} (${path.basename(file)})`;
    notices.push({ name, text: demoteHeadings(readText(file)) });
  }
  for (const entry of embedded) {
    let text;
    try {
      text = readText(entry.license());
    } catch {
      text = undefined; // the licence file is missing: the name and identifier still say what it is
    }
    notices.push({ name: entry.name, identifier: entry.identifier, text });
  }
  const fonts = fontNotices(exportDir);
  notices.push(...fonts.notices);
  return { notices, unlicensedFonts: fonts.unlicensed };
}

/**
 * Adds notices to the licence file Vite made (`build.license`): Markdown sections in Vite's format, or
 * entries of its JSON array when the file name ends in .json.
 * @param {string} source the file as Vite wrote it
 * @param {string} fileName its name in the output
 * @param {Notice[]} notices
 * @returns {string}
 */
export function completeNotices(source, fileName, notices) {
  if (!notices.length) return source;
  if (fileName.endsWith('.json')) {
    /** @type {Notice[]} */
    const entries = JSON.parse(source);
    const known = new Set(entries.map((e) => `${e.name}@${e.version}`));
    entries.push(...notices.filter((n) => !n.version || !known.has(`${n.name}@${n.version}`)));
    return JSON.stringify(entries, null, 2);
  }
  let text = source.includes(VITE_EMPTY) ? source.replace(VITE_EMPTY, VITE_INTRO) : source;
  for (const notice of notices) {
    const heading = `## ${notice.name}${notice.version ? ` - ${notice.version}` : ''}`;
    if (notice.version && text.includes(`\n${heading}`)) continue; // Vite listed it already
    text += `\n${heading}${notice.identifier ? ` (${notice.identifier})` : ''}\n`;
    if (notice.text) text += `\n${notice.text}\n`;
  }
  return text;
}

/**
 * The licences of the fonts in the scene exports, one notice per licence file, and the fonts that have none.
 * @param {string} exportDir
 */
function fontNotices(exportDir) {
  /** @type {Map<string, string[]>} licence file -> the fonts it covers (build paths) */
  const licensed = new Map();
  /** @type {string[]} */
  const unlicensed = [];
  /** @type {string[]} */
  let files;
  try {
    files = readdirSync(exportDir, { recursive: true, encoding: 'utf8' });
  } catch {
    return { notices: [], unlicensed };
  }
  const present = new Set(files.map((f) => path.normalize(f)));
  for (const file of files.sort()) {
    const ext = path.extname(file).toLowerCase();
    if (!FONT_EXTENSIONS.has(ext)) continue;
    const dir = path.dirname(file);
    const stem = path.basename(file, path.extname(file));
    const shown = `__gs/export/${file.split(path.sep).join('/')}`;
    const license = FONT_LICENSES(stem).map((name) => path.normalize(path.join(dir, name))).find((f) => present.has(f));
    if (!license) unlicensed.push(shown);
    else licensed.set(license, [...(licensed.get(license) ?? []), shown]);
  }
  /** @type {Notice[]} */
  const notices = [];
  for (const [license, fonts] of licensed) {
    const text = readText(path.join(exportDir, license));
    /** @type {Notice} */
    const notice = { name: `Font${fonts.length > 1 ? 's' : ''}: ${fonts.join(', ')}`, text };
    if (/SIL OPEN FONT LICENSE/i.test(text) && /Version 1\.1/.test(text)) notice.identifier = 'OFL-1.1';
    notices.push(notice);
  }
  return { notices, unlicensed };
}

/**
 * The package a module belongs to, as Vite finds it: the nearest package.json with a name (in
 * node_modules, the package's own root, not a nested folder's package.json).
 * @param {string} dir
 * @param {Map<string, PackageData | null>} cache
 * @returns {PackageData | null}
 */
function mainPackage(dir, cache) {
  /** @type {string[]} */
  const visited = [];
  let current = dir;
  /** @type {PackageData | null} */
  let found = null;
  for (;;) {
    const known = cache.get(current);
    if (known !== undefined) {
      found = known;
      break;
    }
    visited.push(current);
    const data = readPackage(current);
    if (data && typeof data.name === 'string' && (!IN_NODE_MODULES.test(current) || isPackageRoot(current))) {
      found = { dir: current, data };
      break;
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  for (const d of visited) cache.set(d, found);
  return found;
}

/** @param {string} dir @returns {Record<string, unknown> | null} */
function readPackage(dir) {
  try {
    return JSON.parse(readFileSync(path.join(dir, 'package.json'), 'utf8'));
  } catch {
    return null;
  }
}

/** True when `file` is `dir` or inside it (false on another drive). @param {string} file @param {string} dir */
function inside(file, dir) {
  const rel = path.relative(dir, file);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

/** A package's own folder in node_modules (node_modules/name or node_modules/@scope/name). @param {string} dir */
function isPackageRoot(dir) {
  const parent = path.basename(path.dirname(dir));
  if (parent === 'node_modules') return !path.basename(dir).startsWith('@');
  return parent.startsWith('@') && path.basename(path.dirname(path.dirname(dir))) === 'node_modules';
}

/**
 * The first file in a folder (not below it) whose name passes `test`, or null.
 * @param {string} dir
 * @param {(name: string) => boolean} test
 */
function findFile(dir, test) {
  try {
    const name = readdirSync(dir).sort().find(test);
    return name ? path.join(dir, name) : null;
  } catch {
    return null;
  }
}

/** @param {string} file */
function readText(file) {
  return readFileSync(file, 'utf8').replace(/^﻿/, '').trim();
}

/** A Markdown notices file as part of the build's file: its title left out, its other headings one level down. @param {string} text */
function demoteHeadings(text) {
  return text.replace(/^# .*(\r?\n)+/, '').replace(/^(#{1,5}) /gm, '#$1 ');
}
