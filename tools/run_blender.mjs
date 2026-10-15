// SPDX-License-Identifier: MIT
// Runs Blender headless with a clean factory setup, forwarding the arguments:
//   node tools/run_blender.mjs [--blender PATH] --python some_script.py
// Finds Blender 5.2+ with --blender, $BLENDER, the usual install folders or PATH
// (runtime/scripts/programs.mjs).
import { spawnSync } from 'node:child_process';
import { findBlender } from '../runtime/scripts/programs.mjs';

const args = process.argv.slice(2);
const at = args.indexOf('--blender');
const blender = (at >= 0 ? args.splice(at, 2)[1] : undefined) ?? findBlender() ?? 'blender';

const result = spawnSync(blender, ['--background', '--factory-startup', ...args], {
  stdio: 'inherit',
});
if (result.error) {
  console.error(`Could not run Blender (${result.error.message}). Set BLENDER=path/to/blender.`);
  process.exit(1);
}
process.exit(result.status ?? 1);
