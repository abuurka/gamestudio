#!/usr/bin/env node
// @ts-check
// Writes <project>/.gamestudio/components.json without a running dev server:
// starts Vite in middleware mode (no hub, no listening), generates, closes.
//
// Usage: gamestudio-registry [projectRoot]   (default: current directory)

import path from 'node:path';
import { createServer } from 'vite';
import { registryCounts } from '../src/registry.js';

const root = path.resolve(process.argv[2] ?? process.cwd());

const server = await createServer({
  root,
  logLevel: 'warn',
  appType: 'custom',
  server: { middlewareMode: true, hmr: false, watch: null },
  optimizeDeps: { noDiscovery: true, include: [] },
});

let exitCode = 0;
try {
  const plugin = server.config.plugins.find((p) => p.name === 'gamestudio');
  const api = /** @type {import('../types/index.js').GamestudioPluginAPI | undefined} */ (plugin?.api);
  if (!api) throw new Error(`${root}: vite.config does not use the gamestudio() plugin`);
  const { registry, errors } = await api.generateRegistry();
  for (const { file, message } of errors) console.error(`error: ${file}: ${message}`);
  console.log(`Wrote ${path.join(root, '.gamestudio', 'components.json')} (${registryCounts(registry)})`);
  if (errors.length > 0) exitCode = 1;
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  exitCode = 1;
} finally {
  await server.close();
}
process.exit(exitCode);
