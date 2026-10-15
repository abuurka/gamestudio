// @ts-check
// Serves `.gamestudio/export/**` at `/__gs/export/` (dev) and copies it into
// the build output (build): scenes, `files/` (assets, UI fonts and art) and `ui/`
// (Game UI canvases, docs/GAME-UI.md). Exports change on every Blender export:
// manifests and other JSON (`ui/*.json` too) are never cached, other files are
// revalidated (ETag) on every use, and a GLB requested by its content hash
// (`?h=`, from the manifest's `files`) is cached for good once the hash is
// checked against the file.

import { createHash } from 'node:crypto';
import { cp, mkdir, open, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pipeline } from 'node:stream';

/** @type {Record<string, string>} */
export const MIME_TYPES = {
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.json': 'application/json; charset=utf-8',
  '.bin': 'application/octet-stream',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ktx2': 'image/ktx2',
  '.hdr': 'image/vnd.radiance',
  '.exr': 'image/x-exr',
  '.ogg': 'audio/ogg',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
  // Game UI fonts (FontFace loads them; browsers check the type of woff/woff2).
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

/** URL path (relative to the base) under which exports are served. */
export const EXPORT_URL_PATH = '__gs/export';

/**
 * The Game UI index a project without Blender UI gets (docs/GAME-UI.md §3.1: no canvases). The engine
 * fetches `ui/index.json` at startup; answering it (dev) and writing it (build) keeps a missing one from
 * logging a failed request in every game without UI.
 */
export const EMPTY_UI_INDEX = JSON.stringify({ format: 'gamestudio.ui-index', formatVersion: 1, canvases: {} });
const UI_INDEX = ['ui', 'index.json'];

/** A `?h=` content hash: a SHA-1 hex prefix, long enough to mean something. */
const HASH_QUERY = /^[0-9a-f]{8,40}$/i;
/** For a GLB requested by a hash that matches its content: that URL always gets these bytes. */
const IMMUTABLE = 'public, max-age=31536000, immutable';

/**
 * SHA-1 of each served file by path, with the stat it was computed for (`ino:size:mtime`). Blender
 * replaces export files (a new inode and mtime), so a changed file never matches an old entry.
 * @type {Map<string, { stat: string, sha1: string }>}
 */
const digests = new Map();

/** @param {import('node:fs').Stats} info */
const statKey = (info) => `${info.ino}:${info.size}:${info.mtimeMs}`;

/**
 * Connect middleware; mount it at `/__gs/export` (the mount path is stripped from req.url).
 * @param {string} exportDir absolute path of `.gamestudio/export`
 * @returns {import('vite').Connect.NextHandleFunction}
 */
export function exportMiddleware(exportDir) {
  const base = path.resolve(exportDir);
  return (req, res, next) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') return next();
    let url;
    let pathname;
    try {
      url = new URL(req.url ?? '/', 'http://localhost');
      pathname = decodeURIComponent(url.pathname);
    } catch {
      res.statusCode = 400;
      res.end();
      return;
    }
    const file = path.resolve(base, `.${pathname}`);
    if (!file.startsWith(base + path.sep)) {
      res.statusCode = 403;
      res.end();
      return;
    }
    const index = file === path.join(base, ...UI_INDEX);
    serveFile(req, res, file, url.searchParams.get('h'), index).catch(() => {
      if (!res.headersSent) notFound(res);
      else res.destroy();
    });
  };
}

/**
 * Answers with one export file. The file is opened once: the headers, a hash check and the body all
 * come from that one open file. Answers without a streamed body close it before they are sent; a
 * streamed body closes it when the last byte is read, or when the client goes away mid-transfer. (An
 * open file stops Blender from replacing it on Windows, so none may outlive its request.)
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 * @param {string} file
 * @param {string | null} hash the `h` query
 * @param {boolean} [uiIndex] the file is `ui/index.json`: without one, the empty index answers
 */
async function serveFile(req, res, file, hash, uiIndex = false) {
  /** @type {import('node:fs/promises').FileHandle | null} */
  let handle;
  try {
    handle = await open(file, 'r');
  } catch {
    return uiIndex ? emptyIndex(req, res) : notFound(res);
  }
  const release = async () => {
    const held = handle;
    handle = null;
    await held?.close();
  };
  try {
    const info = await handle.stat();
    if (!info.isFile()) {
      await release();
      return notFound(res);
    }
    const ext = path.extname(file).toLowerCase();
    res.setHeader('Content-Type', MIME_TYPES[ext] ?? 'application/octet-stream');
    /** @type {Buffer | null} the file, when it was read whole to hash it */
    let body = null;
    if (ext === '.json') {
      // Manifests say what to load next: never cached.
      res.setHeader('Cache-Control', 'no-store');
    } else {
      let verified = false;
      if (hash && HASH_QUERY.test(hash)) {
        const known = digests.get(file);
        let sha1 = known?.stat === statKey(info) ? known.sha1 : undefined;
        if (!sha1) {
          body = await handle.readFile();
          sha1 = createHash('sha1').update(body).digest('hex');
          digests.set(file, { stat: statKey(info), sha1 });
        }
        verified = sha1.startsWith(hash.toLowerCase());
        // What was sent, so a game that asked for other content (the file was replaced after its manifest
        // was read) does not keep these bytes under that hash.
        res.setHeader('X-Content-SHA1', sha1);
      }
      // A replaced file (Blender renames a new one over it) has another inode, size or mtime.
      const etag = verified
        ? `"${hash?.toLowerCase()}"`
        : `W/"${info.ino.toString(16)}-${info.size.toString(16)}-${Math.floor(info.mtimeMs).toString(16)}"`;
      res.setHeader('ETag', etag);
      res.setHeader('Cache-Control', verified ? IMMUTABLE : 'no-cache');
      if (matches(req.headers['if-none-match'], etag)) {
        await release();
        res.statusCode = 304;
        res.end();
        return;
      }
    }
    res.statusCode = 200;
    res.setHeader('Content-Length', body?.length ?? info.size);
    if (req.method === 'HEAD' || body) {
      await release();
      res.end(req.method === 'HEAD' ? undefined : body);
      return;
    }
    if (res.destroyed || !res.socket || res.socket.destroyed) return; // the client already left
    const stream = handle.createReadStream({ start: 0 }); // closes the file when it ends or is destroyed
    handle = null;
    // pipeline destroys the read stream (closing the file) when the response closes or fails first.
    pipeline(stream, res, () => {});
  } finally {
    await release();
  }
}

/**
 * `If-None-Match` against an ETag, with the weak comparison HTTP uses for it.
 * @param {string | string[] | undefined} header
 * @param {string} etag
 */
function matches(header, etag) {
  if (!header) return false;
  const bare = (/** @type {string} */ tag) => tag.trim().replace(/^W\//, '');
  const tags = (Array.isArray(header) ? header.join(',') : header).split(',');
  return tags.some((tag) => tag.trim() === '*' || bare(tag) === bare(etag));
}

/**
 * A project without Blender UI: the empty UI index (uncached, like the real one).
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 */
function emptyIndex(req, res) {
  res.statusCode = 200;
  res.setHeader('Content-Type', MIME_TYPES['.json']);
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Length', Buffer.byteLength(EMPTY_UI_INDEX));
  res.end(req.method === 'HEAD' ? undefined : EMPTY_UI_INDEX);
}

/** @param {import('node:http').ServerResponse} res */
function notFound(res) {
  // An explicit 404 (instead of next()) keeps Vite's SPA fallback from answering with index.html.
  res.statusCode = 404;
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.removeHeader('ETag');
  res.end('Not found');
}

/**
 * Copies the export folder into `<outDir>/__gs/export`. Returns false if there is nothing to copy.
 * @param {string} exportDir
 * @param {string} outDir
 */
export async function copyExports(exportDir, outDir) {
  try {
    await stat(exportDir);
  } catch {
    return false;
  }
  const target = path.join(outDir, ...EXPORT_URL_PATH.split('/'));
  await cp(exportDir, target, { recursive: true });
  // A game without Blender UI gets the empty UI index, so loading it never fails (EMPTY_UI_INDEX).
  const index = path.join(target, ...UI_INDEX);
  try {
    await stat(index);
  } catch {
    await mkdir(path.dirname(index), { recursive: true });
    await writeFile(index, EMPTY_UI_INDEX);
  }
  return true;
}
