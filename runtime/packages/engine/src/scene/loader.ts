// Fetches a scene manifest and its GLBs, and parses GLBs with GLTFLoader. With content hashes in the
// manifest (`files`), a GLB that did not change is neither fetched nor parsed again.

import type { Group, Object3D } from 'three';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { cloneScene, ownMaterials } from './clone';

/** Raw (unprocessed) scene files, cached so the scene can be reset without refetching. */
export interface SceneFiles {
  manifestUrl: string;
  /** Parsed JSON exactly as fetched. */
  raw: unknown;
  glb: ArrayBuffer;
  /** Directory URL the GLB was loaded from (for external resources). */
  resourcePath: string;
  /** Prefab template GLBs by their manifest file name (`prefabs[].glb`). */
  prefabGlbs: Map<string, ArrayBuffer>;
  /**
   * Content hashes of the GLBs by file name (the manifest's `files`) that the buffers really have;
   * absent in older exports. Only these buffers are reused, and only their parses are cached.
   */
  hashes?: Record<string, string>;
}

/** Header with the SHA-1 of the bytes the dev server sent for a `?h=` request (export-files.js). */
const SENT_SHA1 = 'x-content-sha1';

export class SceneNotFoundError extends Error {
  constructor(readonly url: string, readonly status: number) {
    super(`Scene manifest not found: ${url} (HTTP ${status}). Export the scene from Blender first.`);
    this.name = 'SceneNotFoundError';
  }
}

/**
 * A GLB with a content hash is requested as `<glb>?h=<hash>` and may come from the HTTP cache (the
 * URL changes when the content does); without one, as `?rev=<rev>`, never cached (older exports).
 * `sha1`: what the dev server says it sent, when it says.
 */
async function fetchGlb(
  file: string,
  base: URL,
  rev: unknown,
  hash: string | undefined,
  what: string,
): Promise<{ buffer: ArrayBuffer; sha1: string | null }> {
  const url = new URL(file, base);
  if (hash) url.searchParams.set('h', hash);
  else url.searchParams.set('rev', String(typeof rev === 'number' ? rev : 0));
  const res = await fetch(url, hash ? {} : { cache: 'no-store' });
  if (!res.ok) throw new Error(`${what} not found: ${url.pathname} (HTTP ${res.status})`);
  return { buffer: await res.arrayBuffer(), sha1: hash ? res.headers.get(SENT_SHA1) : null };
}

/** The distinct `prefabs[].glb` file names of a raw manifest. */
export function prefabGlbFiles(raw: unknown): string[] {
  const prefabs = (raw as { prefabs?: unknown })?.prefabs;
  const files = new Set<string>();
  for (const p of Array.isArray(prefabs) ? prefabs : []) {
    if (p && typeof p.glb === 'string' && p.glb !== '') files.add(p.glb);
  }
  return [...files];
}

/** The manifest's `files` (GLB file name → content hash), or undefined when it has none. */
function glbHashes(raw: unknown): Record<string, string> | undefined {
  const files = (raw as { files?: unknown })?.files;
  if (!files || typeof files !== 'object' || Array.isArray(files)) return undefined;
  const out: Record<string, string> = {};
  for (const [file, hash] of Object.entries(files as Record<string, unknown>)) {
    if (typeof hash === 'string' && hash !== '') out[file] = hash;
  }
  return out;
}

/** The buffer `previous` holds for `file` when it is the same URL with the same content hash. */
function reusable(previous: SceneFiles | null | undefined, file: string, base: URL, hash: string | undefined): ArrayBuffer | undefined {
  if (!hash || !previous || previous.hashes?.[file] !== hash) return undefined;
  if (new URL(file, previous.manifestUrl).href !== new URL(file, base).href) return undefined;
  return file === (previous.raw as { glb?: unknown }).glb ? previous.glb : previous.prefabGlbs.get(file);
}

/**
 * Fetches a manifest (never cached) and its GLBs. GLBs that `previous` (the files of the last load)
 * already holds with the same content hash are reused without a request.
 */
export async function fetchSceneFiles(manifestUrl: string, previous?: SceneFiles | null): Promise<SceneFiles> {
  const absolute = new URL(manifestUrl, location.href);
  const res = await fetch(absolute, { cache: 'no-store' });
  // Static hosts often answer unknown paths with index.html (SPA fallback): treat that as missing too.
  if (!res.ok || (res.headers.get('content-type') ?? '').includes('text/html')) {
    throw new SceneNotFoundError(absolute.pathname, res.status === 200 ? 404 : res.status);
  }
  const raw = (await res.json()) as { glb?: unknown; rev?: unknown };
  if (typeof raw?.glb !== 'string') throw new Error(`Scene manifest ${absolute.pathname} has no "glb" entry`);

  const hashes = glbHashes(raw);
  const get = async (file: string, what: string): Promise<ArrayBuffer> => {
    const hash = hashes?.[file];
    const kept = reusable(previous, file, absolute, hash);
    if (kept) return kept;
    const { buffer, sha1 } = await fetchGlb(file, absolute, raw.rev, hash, what);
    // Blender replaced the file after this manifest was written: these bytes are not the hashed ones.
    if (hash && sha1 && !sha1.startsWith(hash.toLowerCase())) delete hashes![file];
    return buffer;
  };
  const files = prefabGlbFiles(raw);
  const [glb, ...prefabBuffers] = await Promise.all([get(raw.glb, 'Scene GLB'), ...files.map((file) => get(file, 'Prefab GLB'))]);
  const prefabGlbs = new Map(files.map((file, i) => [file, prefabBuffers[i]!]));
  const resourcePath = new URL('.', new URL(raw.glb, absolute)).href;
  return { manifestUrl: absolute.href, raw, glb: glb!, resourcePath, prefabGlbs, ...(hashes && { hashes }) };
}

/**
 * A GLTFLoader that also reads compressed GLBs: `npm run build` compresses geometry with
 * EXT_meshopt_compression (docs/WORKFLOWS.md, "Smaller builds"). The decoder's WASM is inlined in its
 * module, so builds need no decoder files and no CDN. Uncompressed GLBs load as before.
 */
export function createGltfLoader(): GLTFLoader {
  return new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
}

function parseBuffer(buffer: ArrayBuffer, resourcePath: string): Promise<GLTF> {
  // Parse a copy so nothing (e.g. a decoder transferring buffers to a worker) can empty the cache.
  return createGltfLoader().parseAsync(buffer.slice(0), resourcePath);
}

export function parseGlb(files: SceneFiles): Promise<GLTF> {
  return parseBuffer(files.glb, files.resourcePath);
}

/** Parses every prefab GLB; keys are the manifest file names. */
export async function parsePrefabGlbs(files: SceneFiles): Promise<Map<string, GLTF>> {
  const entries = await Promise.all(
    [...files.prefabGlbs].map(async ([file, buffer]) => [file, await parseBuffer(buffer, files.resourcePath)] as const),
  );
  return new Map(entries);
}

/**
 * Parsed GLBs by URL and content hash. A load that finds a file's hash here gets a clone (cloneScene)
 * instead of parsing it again; the cached parse itself never goes into a scene, so it stays as loaded.
 */
export class GltfCache {
  private readonly parsed = new Map<string, GLTF>();

  get size(): number {
    return this.parsed.size;
  }

  /** The cached scene graphs. Their geometries and textures are shared with the clones. */
  roots(): Object3D[] {
    return [...this.parsed.values()].map((gltf) => gltf.scene);
  }

  /**
   * A clone of the parse cached under `key`, parsing (once) with `parse` on a miss. The clone has its
   * own materials, so what the game does to them does not outlive the load.
   */
  async instance(key: string, parse: () => Promise<GLTF>): Promise<GLTF> {
    let gltf = this.parsed.get(key);
    if (!gltf) {
      gltf = await parse();
      this.parsed.set(key, gltf);
    }
    const scene = cloneScene(gltf.scene) as Group;
    ownMaterials(scene);
    return { ...gltf, scene, scenes: [scene] };
  }

  /** Drops the parses whose keys are not in `keep`. Returns their scene graphs, whose resources the caller frees. */
  retain(keep: ReadonlySet<string>): Object3D[] {
    const dropped: Object3D[] = [];
    for (const [key, gltf] of this.parsed) {
      if (keep.has(key)) continue;
      this.parsed.delete(key);
      dropped.push(gltf.scene);
    }
    return dropped;
  }
}

export interface SceneGltfs {
  gltf: GLTF;
  /** Keys are the manifest file names (`prefabs[].glb`). */
  prefabGltfs: Map<string, GLTF>;
  /** Cache keys of the hashed GLBs this load uses (for `GltfCache.retain`). */
  keys: Set<string>;
}

/**
 * Parses the scene GLB and the prefab GLBs. A GLB with a content hash goes through `cache` (a clone
 * of the cached parse, or parsed once and cached); one without (older exports) is parsed as before.
 */
export async function parseSceneGlbs(files: SceneFiles, cache: GltfCache): Promise<SceneGltfs> {
  const keys = new Set<string>();
  const load = (file: string, buffer: ArrayBuffer): Promise<GLTF> => {
    const parse = (): Promise<GLTF> => parseBuffer(buffer, files.resourcePath);
    const hash = files.hashes?.[file];
    if (!hash) return parse();
    const key = `${new URL(file, files.manifestUrl).href}#${hash}`;
    keys.add(key);
    return cache.instance(key, parse);
  };
  const glbFile = String((files.raw as { glb?: unknown }).glb);
  const prefabFiles = [...files.prefabGlbs.keys()];
  const [gltf, ...prefabs] = await Promise.all([
    load(glbFile, files.glb),
    ...prefabFiles.map((file) => load(file, files.prefabGlbs.get(file)!)),
  ]);
  return { gltf: gltf!, prefabGltfs: new Map(prefabFiles.map((file, i) => [file, prefabs[i]!])), keys };
}
