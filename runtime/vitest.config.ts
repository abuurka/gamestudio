import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Runs the tests of every runtime package: `npm test` from the repo root.
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: ['packages/*/test/**/*.test.{ts,js}'],
    environment: 'node',
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
