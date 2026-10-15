#!/usr/bin/env node
// @ts-check
// Compresses scene GLBs the way `npm run build` does (EXT_meshopt_compression, lossless), and prints
// the size before and after per file. The Blender export runs it with --dry-run --json for
// export_scene's `compress` option: the sizes a build would have, with the export left as it is.
//
// Usage: gamestudio-compress [--dry-run] [--json] <export folder | file.glb ...>

import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { compressExports, compressGlb, formatSummary } from '../src/compress.js';

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const asJson = args.includes('--json');
const targets = args.filter((a) => !a.startsWith('--'));
if (!targets.length) {
  console.error('Usage: gamestudio-compress [--dry-run] [--json] <export folder | file.glb ...>');
  process.exit(2);
}

/** @type {import('../types/index.js').CompressResult[]} */
const results = [];
for (const target of targets) {
  if (target.toLowerCase().endsWith('.glb')) {
    const { bytes, ...report } = await compressGlb(new Uint8Array(await readFile(target)));
    if (!dryRun && !report.skipped) await writeFile(target, bytes);
    results.push({ file: path.basename(target), ...report });
  } else {
    results.push(...(await compressExports(target, { dryRun })));
  }
}
if (asJson) console.log(JSON.stringify({ geometry: 'meshopt', dryRun, files: results }));
else for (const line of formatSummary(results, { geometry: 'meshopt', textures: 'none' })) console.log(line.replace('[gamestudio] ', ''));
