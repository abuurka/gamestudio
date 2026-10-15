import { createHash } from 'node:crypto';
import http from 'node:http';
import { existsSync, mkdirSync, mkdtempSync, renameSync, rmSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { exportMiddleware } from '../src/export-files.js';

const dir = mkdtempSync(path.join(tmpdir(), 'gs-export-cache-'));
const GLB = Buffer.from('glTF scene bytes');
const sha1 = (bytes: Buffer) => createHash('sha1').update(bytes).digest('hex');
writeFileSync(path.join(dir, 'main.scene.json'), '{"format":"gamestudio.scene"}');
writeFileSync(path.join(dir, 'main.glb'), GLB);
mkdirSync(path.join(dir, 'files'));

const server = http.createServer((req, res) => {
  req.url = req.url!.replace(/^\/__gs\/export/, '');
  exportMiddleware(dir)(req as never, res, () => {
    res.statusCode = 599;
    res.end();
  });
});
const ready = new Promise<number>((r) => server.listen(0, '127.0.0.1', () => r((server.address() as { port: number }).port)));
afterAll(() => {
  server.close();
  rmSync(dir, { recursive: true, force: true });
});
const get = async (p: string, init: RequestInit = {}) => fetch(`http://127.0.0.1:${await ready}/__gs/export${p}`, init);

/**
 * Renames `from` over `to`, retrying for a while: on Windows that fails while any process has `to`
 * open, and a streamed answer closes its file just after the client has the last byte.
 */
async function renameOver(from: string, to: string, ms = 3000): Promise<unknown> {
  let error: unknown = null;
  for (const until = Date.now() + ms; Date.now() < until; await new Promise((r) => setTimeout(r, 25))) {
    try {
      renameSync(from, to);
      return null;
    } catch (e) {
      error = e;
    }
  }
  return error;
}

/** Blender's way of writing an export: a temp file renamed over the old one. */
async function replace(name: string, bytes: Buffer): Promise<void> {
  const tmp = path.join(dir, `${name}.tmp`);
  writeFileSync(tmp, bytes);
  expect(await renameOver(tmp, path.join(dir, name))).toBeNull();
}

describe('export caching', () => {
  it('revalidates GLBs with an ETag (304 while unchanged) and never caches manifests', async () => {
    const first = await get('/main.glb?rev=3');
    expect(first.status).toBe(200);
    expect(first.headers.get('cache-control')).toBe('no-cache');
    const etag = first.headers.get('etag')!;
    expect(etag).toMatch(/^W\/"[0-9a-f]+-[0-9a-f]+-[0-9a-f]+"$/);
    expect(Buffer.from(await first.arrayBuffer())).toEqual(GLB);

    const again = await get('/main.glb?rev=4', { headers: { 'If-None-Match': etag } });
    expect(again.status).toBe(304);
    expect(await again.text()).toBe('');
    expect(again.headers.get('etag')).toBe(etag);

    await replace('main.glb', Buffer.from('glTF changed'));
    const changed = await get('/main.glb', { headers: { 'If-None-Match': etag } });
    expect(changed.status).toBe(200);
    expect(await changed.text()).toBe('glTF changed');
    await replace('main.glb', GLB);

    const manifest = await get('/main.scene.json', { headers: { 'If-None-Match': '*' } });
    expect(manifest.status).toBe(200);
    expect(manifest.headers.get('cache-control')).toBe('no-store');
    expect(manifest.headers.get('etag')).toBeNull();
  });

  it('caches a GLB for good when ?h= matches its content, and only then', async () => {
    const hash = sha1(GLB).slice(0, 16);
    const hashed = await get(`/main.glb?h=${hash}`);
    expect(hashed.status).toBe(200);
    expect(hashed.headers.get('cache-control')).toBe('public, max-age=31536000, immutable');
    expect(hashed.headers.get('etag')).toBe(`"${hash}"`);
    expect(hashed.headers.get('x-content-sha1')).toBe(sha1(GLB));
    expect(Buffer.from(await hashed.arrayBuffer())).toEqual(GLB);
    // The digest is remembered: the second answer streams the file.
    const head = await get(`/main.glb?h=${hash.toUpperCase()}`, { method: 'HEAD' });
    expect(head.headers.get('content-length')).toBe(String(GLB.length));
    expect(head.headers.get('cache-control')).toMatch(/immutable/);
    expect((await get(`/main.glb?h=${hash}`, { headers: { 'If-None-Match': `"${hash}"` } })).status).toBe(304);

    // A hash of other content (an old manifest, or the file was replaced since): revalidated, not immutable.
    for (const other of ['0123456789abcdef', 'abc', 'not-a-hash']) {
      const res = await get(`/main.glb?h=${other}`);
      expect(res.headers.get('cache-control')).toBe('no-cache');
      expect(res.headers.get('etag')).toMatch(/^W\//);
      // What was really sent, when the query was a hash at all.
      expect(res.headers.get('x-content-sha1')).toBe(/^[0-9a-f]{8,}$/.test(other) ? sha1(GLB) : null);
      expect(Buffer.from(await res.arrayBuffer())).toEqual(GLB);
    }
    const next = Buffer.from('glTF next export');
    await replace('main.glb', next);
    const stale = await get(`/main.glb?h=${hash}`);
    expect(stale.headers.get('cache-control')).toBe('no-cache');
    expect(await stale.text()).toBe('glTF next export');
    expect((await get(`/main.glb?h=${sha1(next).slice(0, 12)}`)).headers.get('cache-control')).toMatch(/immutable/);
    await replace('main.glb', GLB);
  });

  it('closes the file when a client goes away mid-download, so it can be replaced or deleted', async () => {
    const big = path.join(dir, 'big.glb');
    writeFileSync(big, Buffer.alloc(32 * 1024 * 1024, 7));
    const port = await ready;
    await new Promise<void>((resolve, reject) => {
      const req = http.get(`http://127.0.0.1:${port}/__gs/export/big.glb`, (res) => {
        expect(res.statusCode).toBe(200);
        res.once('data', () => {
          req.destroy(); // a page that navigates away, a playtest that closes
          resolve();
        });
      });
      req.on('error', (error) => {
        if (!req.destroyed) reject(error);
      });
    });
    // On Windows a file still open in the server cannot be renamed over (WinError 5 in Blender).
    writeFileSync(`${big}.tmp`, Buffer.from('glTF small'));
    expect(await renameOver(`${big}.tmp`, big)).toBeNull();
    unlinkSync(big);
    expect(existsSync(big)).toBe(false);
  });

  it('answers a missing ui/index.json with the empty index (no failed request in games without UI)', async () => {
    const res = await get('/ui/index.json');
    expect([res.status, res.headers.get('cache-control')]).toEqual([200, 'no-store']);
    expect(res.headers.get('content-type')).toMatch(/^application\/json/);
    expect(await res.json()).toEqual({ format: 'gamestudio.ui-index', formatVersion: 1, canvases: {} });
    expect((await get('/ui/Title.ui.json')).status).toBe(404); // only the index has a stand-in
  });

  it('serves Game UI canvases uncached and their fonts and art with their types', async () => {
    mkdirSync(path.join(dir, 'ui'), { recursive: true });
    writeFileSync(path.join(dir, 'ui', 'index.json'), '{"format":"gamestudio.ui-index"}');
    mkdirSync(path.join(dir, 'files', 'assets', 'fonts'), { recursive: true });
    mkdirSync(path.join(dir, 'files', 'assets', 'ui'), { recursive: true });
    const types: Record<string, string> = { 'Inter.woff2': 'font/woff2', 'Baloo.ttf': 'font/ttf', 'Serif.otf': 'font/otf', 'Old.woff': 'font/woff' };
    for (const name of Object.keys(types)) writeFileSync(path.join(dir, 'files', 'assets', 'fonts', name), 'font');
    writeFileSync(path.join(dir, 'files', 'assets', 'ui', 'logo.svg'), '<svg/>');
    const index = await get('/ui/index.json');
    expect([index.status, index.headers.get('cache-control')]).toEqual([200, 'no-store']);
    expect(index.headers.get('content-type')).toMatch(/^application\/json/);
    for (const [name, type] of Object.entries(types)) {
      expect((await get(`/files/assets/fonts/${name}?v=0123456789`)).headers.get('content-type')).toBe(type);
    }
    expect((await get('/files/assets/ui/logo.svg')).headers.get('content-type')).toBe('image/svg+xml');
  });

  it('answers 404 for folders and missing files', async () => {
    expect((await get('/files')).status).toBe(404);
    expect((await get('/nope.glb?h=0123456789abcdef')).status).toBe(404);
  });
});
