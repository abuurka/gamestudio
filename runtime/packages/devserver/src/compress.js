// @ts-check
// Geometry compression for builds (docs/WORKFLOWS.md, "Smaller builds"): `npm run build` rewrites the
// scene GLBs it copies into the build with EXT_meshopt_compression. Development keeps the plain GLBs
// Blender writes, so exports and hot reloads stay fast.
//
// Only buffer views change. Vertex attributes, indices, animation keys, inverse bind matrices and
// GPU-instancing transforms are encoded with meshopt's lossless codecs and checked by decoding them
// again; a view keeps its plain data when encoding would not pay off with or without gzip (worthIt).
// Images, sparse data and views another extension points at stay as they are. The JSON
// (node order, names, extras.gs_id, skins, morph targets, animations, EXT_mesh_gpu_instancing, a
// second UV set, extensions this file does not know) is untouched, so the manifest's node references
// and colliders built from meshes match the uncompressed export. The engine registers three.js's
// MeshoptDecoder, whose WASM is inlined in the module: no decoder files to serve, no CDN.

import { readdir, readFile, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { gzipSync } from 'node:zlib';
import { MeshoptDecoder } from 'meshoptimizer/decoder';
import { MeshoptEncoder } from 'meshoptimizer/encoder';

export const MESHOPT = 'EXT_meshopt_compression';
/** Values of gamestudio.json `compression.geometry`. */
export const GEOMETRY_MODES = /** @type {const} */ (['meshopt', 'none']);
/** Values of gamestudio.json `compression.textures` (applied by the Blender export). */
export const TEXTURE_MODES = /** @type {const} */ (['webp', 'none']);
export const DEFAULT_COMPRESSION = /** @type {const} */ ({ geometry: 'meshopt', textures: 'none' });

const GLB_MAGIC = 0x46546c67;
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;
const TRIANGLES = 4;
/** @type {Record<number, number>} */
const COMPONENT_BYTES = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
/** @type {Record<string, number>} */
const WIDTHS = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };

/** @typedef {import('../types/index.js').CompressionConfig} CompressionConfig */
/** @typedef {import('../types/index.js').CompressResult} CompressResult */

/**
 * The `compression` section of gamestudio.json with defaults; unknown values fall back with a warning.
 * @param {unknown} section
 * @param {{ warn(message: string): void }} [logger]
 * @returns {CompressionConfig}
 */
export function compressionConfig(section, logger) {
  /** @type {CompressionConfig} */
  const out = { ...DEFAULT_COMPRESSION };
  if (section === undefined || section === null) return out;
  if (typeof section !== 'object' || Array.isArray(section)) {
    logger?.warn('[gamestudio] gamestudio.json: "compression" must be an object; using the defaults');
    return out;
  }
  const value = /** @type {Record<string, unknown>} */ (section);
  for (const [key, modes] of /** @type {const} */ ([['geometry', GEOMETRY_MODES], ['textures', TEXTURE_MODES]])) {
    const mode = value[key];
    if (mode === undefined) continue;
    if (typeof mode === 'string' && /** @type {readonly string[]} */ (modes).includes(mode)) {
      out[key] = /** @type {any} */ (mode);
    } else {
      logger?.warn(`[gamestudio] gamestudio.json: compression.${key} = ${JSON.stringify(mode)} is not one of ${modes.join(', ')}; using "${out[key]}"`);
    }
  }
  return out;
}

/**
 * Splits a GLB into its JSON and BIN chunks, or returns null when it is not a glTF 2.0 binary.
 * @param {Uint8Array} bytes
 * @returns {{ json: any, bin: Uint8Array | null } | null}
 */
export function readGlb(bytes) {
  if (bytes.byteLength < 20) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(4, true) !== 2) return null;
  const length = Math.min(view.getUint32(8, true), bytes.byteLength);
  let json = null;
  /** @type {Uint8Array | null} */
  let bin = null;
  for (let offset = 12; offset + 8 <= length; ) {
    const size = view.getUint32(offset, true);
    const type = view.getUint32(offset + 4, true);
    const body = bytes.subarray(offset + 8, offset + 8 + size);
    if (type === CHUNK_JSON && json === null) json = JSON.parse(new TextDecoder().decode(body));
    else if (type === CHUNK_BIN && bin === null) bin = body;
    offset += 8 + size;
  }
  return json === null ? null : { json, bin };
}

/**
 * A GLB from its JSON and BIN chunk.
 * @param {any} json
 * @param {Uint8Array | null} bin
 */
export function writeGlb(json, bin) {
  const text = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = text.length + (-text.length & 3);
  const binLength = bin ? bin.length + (-bin.length & 3) : 0;
  const total = 12 + 8 + jsonLength + (bin ? 8 + binLength : 0);
  const out = new Uint8Array(total);
  const view = new DataView(out.buffer);
  view.setUint32(0, GLB_MAGIC, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, CHUNK_JSON, true);
  out.set(text, 20);
  out.fill(0x20, 20 + text.length, 20 + jsonLength);
  if (bin) {
    const at = 20 + jsonLength;
    view.setUint32(at, binLength, true);
    view.setUint32(at + 4, CHUNK_BIN, true);
    out.set(bin, at + 8);
  }
  return out;
}

/**
 * Every `bufferView` reference in the JSON that is not an accessor's own (images, sparse accessors,
 * extensions): those views are left uncompressed.
 * @param {any} json
 */
function foreignViews(json) {
  /** @type {Set<number>} */
  const found = new Set();
  /** @param {unknown} value @param {boolean} accessorRoot */
  const visit = (value, accessorRoot) => {
    if (Array.isArray(value)) {
      for (const item of value) visit(item, false);
    } else if (value && typeof value === 'object') {
      for (const [key, item] of Object.entries(value)) {
        if (key === 'bufferView' && typeof item === 'number' && !accessorRoot) found.add(item);
        else visit(item, false);
      }
    }
  };
  for (const [key, value] of Object.entries(json)) {
    if (key === 'accessors' && Array.isArray(value)) for (const accessor of value) visit(accessor, true);
    else if (key !== 'bufferViews' && key !== 'buffers') visit(value, false);
  }
  return found;
}

/**
 * How each buffer view can be encoded: { mode, stride, count } per view index, for the views that
 * only accessors use and whose layout meshopt accepts.
 * @param {any} json
 */
function plan(json) {
  const views = json.bufferViews ?? [];
  const accessors = json.accessors ?? [];
  /** @type {Map<number, 'TRIANGLES' | 'INDICES'>} */
  const indexRole = new Map();
  for (const mesh of json.meshes ?? []) {
    for (const prim of mesh.primitives ?? []) {
      if (typeof prim.indices !== 'number') continue;
      const mode = (prim.mode ?? TRIANGLES) === TRIANGLES ? 'TRIANGLES' : 'INDICES';
      indexRole.set(prim.indices, indexRole.get(prim.indices) === 'INDICES' ? 'INDICES' : mode);
    }
  }
  const banned = foreignViews(json);
  /** @type {Map<number, { mode: string, sizes: Set<number> }>} */
  const users = new Map();
  for (let i = 0; i < accessors.length; i++) {
    const accessor = accessors[i];
    if (typeof accessor.bufferView !== 'number') continue;
    const role = indexRole.get(i) ?? 'ATTRIBUTES';
    let entry = users.get(accessor.bufferView);
    if (!entry) users.set(accessor.bufferView, (entry = { mode: role, sizes: new Set() }));
    else if (entry.mode !== role) {
      // Index lists of several kinds share it: INDICES fits them all. Indices and vertex data: leave it.
      if (entry.mode === 'ATTRIBUTES' || role === 'ATTRIBUTES') banned.add(accessor.bufferView);
      entry.mode = 'INDICES';
    }
    const width = WIDTHS[accessor.type];
    const bytes = COMPONENT_BYTES[accessor.componentType];
    if (!width || !bytes) banned.add(accessor.bufferView);
    else entry.sizes.add(role === 'ATTRIBUTES' ? width * bytes : bytes);
  }
  /** @type {Map<number, { mode: string, stride: number, count: number }>} */
  const out = new Map();
  for (const [index, entry] of users) {
    const view = views[index];
    if (banned.has(index) || !view || (view.buffer ?? 0) !== 0 || view.extensions) continue;
    const stride = entry.mode === 'ATTRIBUTES' ? (view.byteStride ?? (entry.sizes.size === 1 ? [...entry.sizes][0] : 0)) : [...entry.sizes][0];
    if (!stride || (entry.mode !== 'ATTRIBUTES' && entry.sizes.size !== 1)) continue;
    if (entry.mode === 'ATTRIBUTES' ? stride % 4 !== 0 || stride > 256 : stride !== 2 && stride !== 4) continue;
    const length = view.byteLength ?? 0;
    if (!length || length % stride !== 0) continue;
    const count = length / stride;
    const mode = entry.mode === 'TRIANGLES' && count % 3 !== 0 ? 'INDICES' : entry.mode;
    out.set(index, { mode, stride, count });
  }
  return out;
}

/**
 * True when `decoded` holds the same data as `source`: byte for byte, or for triangle lists the same
 * triangles in the same order and winding (the index codec may rotate a triangle's corners).
 * @param {Uint8Array} source @param {Uint8Array} decoded @param {string} mode @param {number} stride
 */
function sameData(source, decoded, mode, stride) {
  if (source.length !== decoded.length) return false;
  if (mode !== 'TRIANGLES') return Buffer.compare(Buffer.from(source.buffer, source.byteOffset, source.length), Buffer.from(decoded.buffer, decoded.byteOffset, decoded.length)) === 0;
  const read = (/** @type {Uint8Array} */ data) =>
    stride === 2 ? new Uint16Array(data.slice().buffer) : new Uint32Array(data.slice().buffer);
  const a = read(source);
  const b = read(decoded);
  for (let i = 0; i < a.length; i += 3) {
    const [x, y, z] = [a[i], a[i + 1], a[i + 2]];
    const [p, q, r] = [b[i], b[i + 1], b[i + 2]];
    if (!((x === p && y === q && z === r) || (x === q && y === r && z === p) || (x === r && y === p && z === q))) return false;
  }
  return true;
}

/**
 * Whether the meshopt stream should replace a view's data: it must be smaller, and stay about as small
 * once a web server gzips the file. Most static hosts send .glb files uncompressed, some gzip them, and
 * raw floats with many repeats (the flat-shaded normals of low-poly meshes) gzip better than meshopt's
 * output does. A view is encoded when its gzipped size grows by less than a quarter of what encoding
 * saves without gzip, so the build shrinks either way.
 * @param {Uint8Array} source @param {Uint8Array} data
 */
function worthIt(source, data) {
  if (data.length >= source.length) return false;
  const zipped = (/** @type {Uint8Array} */ bytes) => gzipSync(bytes, { level: 6 }).length;
  return zipped(data) - zipped(source) <= (source.length - data.length) / 4;
}

/**
 * Bytes of the embedded images and how many there are of each format ({ png: 2, webp: 3 }).
 * @param {any} json
 */
function glbInfo(json) {
  const views = json.bufferViews ?? [];
  /** @type {Record<string, number>} */
  const images = {};
  let textures = 0;
  for (const image of json.images ?? []) {
    if (typeof image.bufferView === 'number' && views[image.bufferView]) textures += views[image.bufferView].byteLength ?? 0;
    const mime = String(image.mimeType ?? path.extname(String(image.uri ?? '').split('?')[0] ?? '').slice(1));
    const kind = (mime.split('/').pop() || 'other').replace('jpeg', 'jpg');
    images[kind] = (images[kind] ?? 0) + 1;
  }
  return { textures, images };
}

/**
 * Compresses a GLB's geometry with EXT_meshopt_compression (lossless). GLBs that are already
 * compressed, use Draco, have external buffers or no geometry come back unchanged with `skipped`.
 * @param {Uint8Array} bytes
 * @returns {Promise<{ bytes: Uint8Array } & Omit<CompressResult, 'file'>>}
 */
export async function compressGlb(bytes) {
  const glb = readGlb(bytes);
  /** @param {string} reason @param {Partial<CompressResult>} [extra] */
  const unchanged = (reason, extra = {}) => ({
    bytes,
    before: bytes.length,
    after: bytes.length,
    geometryBefore: 0,
    geometryAfter: 0,
    textures: 0,
    images: {},
    views: 0,
    skipped: reason,
    ...extra,
  });
  if (!glb) return unchanged('not a glTF 2.0 binary');
  const { json, bin } = glb;
  const views = json.bufferViews ?? [];
  const info = glbInfo(json);
  const used = json.extensionsUsed ?? [];
  if (used.includes(MESHOPT)) return unchanged('already compressed', info);
  if (used.includes('KHR_meshopt_compression')) return unchanged('already compressed (KHR_meshopt_compression)', info);
  if (used.includes('KHR_draco_mesh_compression')) return unchanged('uses Draco compression', info);
  const buffers = json.buffers ?? [];
  if (!bin || buffers.length !== 1 || buffers[0].uri !== undefined) return unchanged('no single embedded buffer', info);

  const encodable = plan(json);
  if (!encodable.size) return unchanged('no geometry to compress', info);
  await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);

  /** @type {Map<number, { data: Uint8Array, mode: string, stride: number, count: number }>} */
  const encoded = new Map();
  let geometryBefore = 0;
  let geometryAfter = 0;
  for (const [index, { mode, stride, count }] of encodable) {
    const view = views[index];
    const start = view.byteOffset ?? 0;
    const source = bin.slice(start, start + view.byteLength);
    geometryBefore += source.length;
    let data;
    try {
      data = MeshoptEncoder.encodeGltfBuffer(source, count, stride, mode);
      const decoded = new Uint8Array(count * stride);
      MeshoptDecoder.decodeGltfBuffer(decoded, count, stride, data, mode);
      if (!sameData(source, decoded, mode, stride)) data = null;
    } catch {
      data = null;
    }
    if (data && worthIt(source, data)) {
      encoded.set(index, { data, mode, stride, count });
      geometryAfter += data.length;
    } else {
      geometryAfter += source.length;
    }
  }
  if (!encoded.size) return unchanged('compression saved nothing', { ...info, geometryBefore, geometryAfter });

  // The BIN chunk: uncompressed views at their new offsets, compressed streams for the others. The
  // compressed views point at a fallback buffer (no data: the extension is required) at their old
  // offsets, which keeps their layout valid for any reader that checks it.
  const parts = [];
  let length = 0;
  for (let index = 0; index < views.length; index++) {
    const view = views[index];
    if ((view.buffer ?? 0) !== 0) continue;
    const start = view.byteOffset ?? 0;
    const entry = encoded.get(index);
    const data = entry ? entry.data : bin.subarray(start, start + view.byteLength);
    const pad = -length & 3;
    if (pad) parts.push(new Uint8Array(pad));
    length += pad;
    if (entry) {
      view.buffer = 1;
      view.extensions = {
        ...view.extensions,
        [MESHOPT]: { buffer: 0, byteOffset: length, byteLength: data.length, byteStride: entry.stride, count: entry.count, mode: entry.mode },
      };
    } else {
      view.byteOffset = length;
    }
    parts.push(data);
    length += data.length;
  }
  const out = new Uint8Array(length + (-length & 3));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  buffers[0].byteLength = out.length;
  buffers.push({ byteLength: bin.length, extensions: { [MESHOPT]: { fallback: true } } });
  for (const key of ['extensionsUsed', 'extensionsRequired']) {
    const list = (json[key] ??= []);
    if (!list.includes(MESHOPT)) list.push(MESHOPT);
  }
  const result = writeGlb(json, out);
  if (result.length >= bytes.length) return unchanged('compression saved nothing', { ...info, geometryBefore, geometryAfter });
  return { bytes: result, before: bytes.length, after: result.length, geometryBefore, geometryAfter, views: encoded.size, ...info };
}

/**
 * Compresses the scene GLBs (top level of `dir`: `<scene>.glb`, `<scene>.prefabs.glb`) in place.
 * Asset files under files/ are left alone (game code may load them with its own loader).
 * @param {string} dir
 * @param {{ geometry?: string, dryRun?: boolean }} [options] dryRun: report without writing
 * @returns {Promise<CompressResult[]>}
 */
export async function compressExports(dir, options = {}) {
  let names;
  try {
    names = (await readdir(dir)).filter((name) => name.toLowerCase().endsWith('.glb')).sort();
  } catch {
    return [];
  }
  /** @type {CompressResult[]} */
  const results = [];
  for (const name of names) {
    const file = path.join(dir, name);
    if (!(await stat(file)).isFile()) continue;
    const bytes = new Uint8Array(await readFile(file));
    if (options.geometry === 'none') {
      const info = glbInfo(readGlb(bytes)?.json ?? {});
      results.push({ file: name, before: bytes.length, after: bytes.length, geometryBefore: 0, geometryAfter: 0, views: 0, ...info, skipped: 'compression.geometry is "none"' });
      continue;
    }
    const { bytes: out, ...report } = await compressGlb(bytes);
    if (out !== bytes && !options.dryRun) await writeFile(file, out);
    results.push({ file: name, ...report });
  }
  return results;
}

/** @param {number} count */
export function formatBytes(count) {
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(1)} KB`;
  return `${(count / 1024 / 1024).toFixed(2)} MB`;
}

/**
 * The build's size summary: one line per GLB (before -> after, geometry, textures) and the total.
 * @param {CompressResult[]} results
 * @param {CompressionConfig} config
 * @returns {string[]}
 */
export function formatSummary(results, config) {
  if (!results.length) return [];
  const width = Math.max(5, ...results.map((r) => r.file.length));
  const pct = (/** @type {number} */ a, /** @type {number} */ b) => (a ? `${b <= a ? '-' : '+'}${Math.round((Math.abs(a - b) / a) * 100)}%` : '');
  const lines = [`[gamestudio] scene files (geometry: ${config.geometry}, textures: ${config.textures}):`];
  let before = 0;
  let after = 0;
  for (const r of results) {
    before += r.before;
    after += r.after;
    const images = Object.entries(r.images).map(([kind, n]) => `${n} ${kind}`).join(', ');
    const parts = [`${formatBytes(r.before).padStart(9)} -> ${formatBytes(r.after).padStart(9)} ${pct(r.before, r.after).padStart(5)}`];
    if (r.views) parts.push(`geometry ${formatBytes(r.geometryBefore)} -> ${formatBytes(r.geometryAfter)}`);
    else if (r.skipped) parts.push(`(${r.skipped})`);
    if (r.textures) parts.push(`textures ${formatBytes(r.textures)}${images ? ` (${images})` : ''}`);
    lines.push(`  ${r.file.padEnd(width)}  ${parts.join(', ')}`);
  }
  lines.push(`  ${'total'.padEnd(width)}  ${formatBytes(before).padStart(9)} -> ${formatBytes(after).padStart(9)} ${pct(before, after).padStart(5)}`);
  const png = results.reduce((n, r) => n + (r.images.png ?? 0) + (r.images.jpg ?? 0), 0);
  if (png && config.textures === 'none') {
    lines.push(`  ${png} PNG/JPEG texture(s): "compression": {"textures": "webp"} in gamestudio.json makes the export write WebP`);
  }
  return lines;
}
