// The shader gallery's dev server (node tools/shader-gallery/capture.mjs starts it, or
// `npx vite tools/shader-gallery --config tools/shader-gallery/vite.config.mjs` to browse).
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const here = fileURLToPath(new URL('.', import.meta.url));
const repo = fileURLToPath(new URL('../../', import.meta.url));

export default defineConfig({
  root: here,
  // Scratch stays out of the repo tree (.dev is ignored) and off C:.
  cacheDir: `${repo}.dev/tmp/shader-gallery/vite-cache`,
  // capture.mjs reports the page's console itself.
  server: { fs: { allow: [repo] }, forwardConsole: false },
  logLevel: 'warn',
  clearScreen: false,
});
