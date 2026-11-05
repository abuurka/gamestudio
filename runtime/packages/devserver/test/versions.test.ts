import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// The @gamestudio packages carry the extension's version (blender_manifest.toml), so a bug report
// names one number and a project's gamestudio.json "engine" matches the GameStudio that made it.
// Bump all of them together; then run `npm install --package-lock-only` and `python tools/runtime_lock.py`.

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const PACKAGES = ['devserver', 'engine', 'host', 'mcp'];

const json = (relative: string) => JSON.parse(readFileSync(path.join(REPO, relative), 'utf8'));

function extensionVersion(): string {
  const manifest = readFileSync(path.join(REPO, 'blender/gamestudio/blender_manifest.toml'), 'utf8');
  const match = /^version\s*=\s*"([^"]+)"/m.exec(manifest);
  if (!match) throw new Error('no version in blender_manifest.toml');
  return match[1];
}

describe('versions', () => {
  const version = extensionVersion();

  it.each(PACKAGES)('@gamestudio/%s has the extension version', (name) => {
    expect(json(`runtime/packages/${name}/package.json`).version).toBe(version);
  });

  it('internal dependencies ask for that version', () => {
    const manifests = [...PACKAGES.map((name) => `runtime/packages/${name}/package.json`), 'examples/demo-game/package.json'];
    for (const file of manifests) {
      const pkg = json(file);
      for (const field of ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies']) {
        for (const [dep, range] of Object.entries<string>(pkg[field] ?? {})) {
          if (dep.startsWith('@gamestudio/')) expect(`${file} ${dep}@${range}`).toBe(`${file} ${dep}@${version}`);
        }
      }
    }
  });

  it('the lockfiles agree', () => {
    const lock = json('package-lock.json').packages;
    for (const name of PACKAGES) expect(lock[`runtime/packages/${name}`]?.version).toBe(version);
    const runtime = json('blender/gamestudio/core/runtime.lock.json').packages;
    for (const name of ['devserver', 'engine', 'mcp']) expect(runtime[`packages/${name}`]?.version).toBe(version);
  });
});
