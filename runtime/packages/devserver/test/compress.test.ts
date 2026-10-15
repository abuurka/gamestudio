import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BufferAttribute, InstancedMesh, Mesh, Object3D, SkinnedMesh } from 'three';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { describe, expect, it } from 'vitest';
import { compressExports, compressGlb, compressionConfig, formatSummary, MESHOPT, readGlb, writeGlb } from '../src/compress.js';

const FIXTURE = fileURLToPath(new URL('../../../fixtures/basic/basic.glb', import.meta.url));
const FLOAT = 5126;
const UBYTE = 5121;
const USHORT = 5123;
const UINT = 5125;
// A 1x1 PNG: images must pass through untouched.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

/** A GLB with what an export can hold: entities, GPU instances, a skin, a morph target, clips, two UV sets. */
function fixtureGlb(): Uint8Array {
  const chunks: Uint8Array[] = [];
  const bufferViews: Record<string, unknown>[] = [];
  const accessors: Record<string, unknown>[] = [];
  let length = 0;
  const view = (bytes: Uint8Array, extra: Record<string, unknown> = {}) => {
    const pad = -length & 3;
    if (pad) chunks.push(new Uint8Array(pad));
    length += pad;
    bufferViews.push({ buffer: 0, byteOffset: length, byteLength: bytes.length, ...extra });
    chunks.push(bytes);
    length += bytes.length;
    return bufferViews.length - 1;
  };
  const accessor = (data: Float32Array | Uint16Array | Uint32Array | Uint8Array, type: string, componentType: number, extra: Record<string, unknown> = {}) => {
    const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }[type]!;
    const bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    accessors.push({ bufferView: view(bytes.slice()), componentType, count: data.length / width, type, ...extra });
    return accessors.length - 1;
  };
  // Smooth values, like real meshes (meshopt codes deltas between neighbouring vertices).
  const random = (n: number, seed: number) => Float32Array.from({ length: n }, (_, i) => Math.round(Math.sin(i * 0.05 + seed) * 200) / 100);
  const bounds = (data: Float32Array) => {
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < data.length; i++) {
      min[i % 3] = Math.min(min[i % 3]!, data[i]!);
      max[i % 3] = Math.max(max[i % 3]!, data[i]!);
    }
    return { min, max };
  };
  const vertices = 64;
  const triangles = Uint16Array.from({ length: 90 }, (_, i) => (i * 7 + (i % 3)) % vertices);
  const positions = random(vertices * 3, 1);
  const box = {
    attributes: {
      POSITION: accessor(positions, 'VEC3', FLOAT, bounds(positions)),
      NORMAL: accessor(random(vertices * 3, 2).map((v) => v / 2), 'VEC3', FLOAT),
      TEXCOORD_0: accessor(random(vertices * 2, 3), 'VEC2', FLOAT),
      TEXCOORD_1: accessor(random(vertices * 2, 4).map((v) => (v + 2) / 4), 'VEC2', FLOAT), // a lightmap UV set
    },
    indices: accessor(triangles, 'SCALAR', USHORT, { }),
  };
  const morphPositions = random(vertices * 3, 5);
  const morph = {
    attributes: { POSITION: accessor(morphPositions, 'VEC3', FLOAT, bounds(morphPositions)) },
    indices: accessor(triangles.slice(), 'SCALAR', USHORT),
    targets: [{ POSITION: accessor(random(vertices * 3, 6).map((v) => v / 10), 'VEC3', FLOAT, bounds(random(vertices * 3, 6).map((v) => v / 10))) }],
  };
  const skinPositions = random(vertices * 3, 7);
  const skinned = {
    attributes: {
      POSITION: accessor(skinPositions, 'VEC3', FLOAT, bounds(skinPositions)),
      JOINTS_0: accessor(Uint8Array.from({ length: vertices * 4 }, (_, i) => (i % 4 === 0 ? i % 2 : 0)), 'VEC4', UBYTE),
      WEIGHTS_0: accessor(Float32Array.from({ length: vertices * 4 }, (_, i) => (i % 4 === 0 ? 1 : 0)), 'VEC4', FLOAT),
    },
    indices: accessor(Uint32Array.from(triangles), 'SCALAR', UINT),
  };
  const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const inverseBind = accessor(Float32Array.from([...identity, ...identity.map((v, i) => (i === 13 ? -1 : v))]), 'MAT4', FLOAT);
  const instances = accessor(Float32Array.from({ length: 15 }, (_, i) => i * 1.5), 'VEC3', FLOAT);
  const times = accessor(Float32Array.from([0, 0.5, 1]), 'SCALAR', FLOAT, { min: [0], max: [1] });
  const moves = accessor(Float32Array.from([0, 0, 0, 0, 1, 0, 0, 0, 0]), 'VEC3', FLOAT);
  const turns = accessor(Float32Array.from([0, 0, 0, 1, 0, 0.7071068, 0, 0.7071068, 0, 0, 0, 1]), 'VEC4', FLOAT);
  const weights = accessor(Float32Array.from([0, 1, 0]), 'SCALAR', FLOAT);
  const image = view(new Uint8Array(PNG));
  const lightmapData = view(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8])); // another tool's extension points at it

  const json = {
    asset: { version: '2.0', generator: 'gamestudio compress test' },
    extensionsUsed: ['EXT_mesh_gpu_instancing', 'GS_test_extension'],
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [
      { name: 'Root', children: [1, 2, 3, 4] },
      { name: 'Crate', mesh: 0, translation: [1, 2, 3], extras: { gs_id: 'gs_0000000000c1' } },
      { name: 'Scatter.Box.instances', mesh: 0, extensions: { EXT_mesh_gpu_instancing: { attributes: { TRANSLATION: instances } } } },
      { name: 'Blob', mesh: 1, weights: [0], extras: { gs_id: 'gs_0000000000c2' } },
      { name: 'Armature', children: [5, 6], extras: { gs_id: 'gs_0000000000c3' } },
      { name: 'Body', mesh: 2, skin: 0 },
      { name: 'Bone0', children: [7] },
      { name: 'Bone1', translation: [0, 1, 0], extensions: { GS_test_extension: { bufferView: lightmapData } } },
    ],
    meshes: [
      { name: 'Box', primitives: [box] },
      { name: 'Morph', primitives: [morph], weights: [0] },
      { name: 'Skinned', primitives: [skinned] },
    ],
    skins: [{ joints: [6, 7], inverseBindMatrices: inverseBind }],
    animations: [
      {
        name: 'Wave',
        samplers: [
          { input: times, output: moves },
          { input: times, output: turns },
          { input: times, output: weights },
        ],
        channels: [
          { sampler: 0, target: { node: 1, path: 'translation' } },
          { sampler: 1, target: { node: 7, path: 'rotation' } },
          { sampler: 2, target: { node: 3, path: 'weights' } },
        ],
      },
    ],
    images: [{ name: 'Lightmap', mimeType: 'image/png', bufferView: image }],
    accessors,
    bufferViews,
    buffers: [{ byteLength: length + (-length & 3) }],
  };
  const bin = new Uint8Array(length + (-length & 3));
  let at = 0;
  for (const chunk of chunks) {
    bin.set(chunk, at);
    at += chunk.length;
  }
  return writeGlb(json, bin);
}

function parse(bytes: Uint8Array): Promise<GLTF> {
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).parseAsync(buffer, '');
}

/** Everything about a loaded scene that compression must keep: names, ids, transforms, geometry, clips. */
function describe3d(gltf: GLTF) {
  const objects: unknown[] = [];
  gltf.scene.traverse((o: Object3D) => {
    const entry: Record<string, unknown> = {
      name: o.name,
      type: o.type,
      gs_id: o.userData.gs_id,
      matrix: o.matrix.elements.map((v) => Math.round(v * 1e6) / 1e6),
    };
    if (o instanceof Mesh) {
      const geometry = o.geometry;
      entry.attributes = Object.fromEntries(
        Object.entries(geometry.attributes).map(([k, a]) => [k, Array.from((a as BufferAttribute).array)]),
      );
      entry.morph = Object.fromEntries(
        Object.entries(geometry.morphAttributes).map(([k, list]) => [k, (list as BufferAttribute[]).map((a) => Array.from(a.array))]),
      );
      // The index codec may rotate a triangle's corners: compare triangles by their smallest-first rotation.
      const index: number[] = geometry.index ? Array.from(geometry.index.array as ArrayLike<number>) : [];
      const tris = [];
      for (let i = 0; i < index.length; i += 3) {
        const t = [index[i]!, index[i + 1]!, index[i + 2]!];
        const k = t.indexOf(Math.min(...t));
        tris.push([t[k], t[(k + 1) % 3], t[(k + 2) % 3]]);
      }
      entry.triangles = tris;
    }
    if (o instanceof InstancedMesh) entry.instances = Array.from(o.instanceMatrix.array);
    if (o instanceof SkinnedMesh) entry.bones = o.skeleton.bones.map((b) => b.name);
    objects.push(entry);
  });
  const clips = gltf.animations.map((clip) => ({
    name: clip.name,
    duration: clip.duration,
    tracks: clip.tracks.map((t) => ({ name: t.name, times: Array.from(t.times), values: Array.from(t.values) })),
  }));
  return { objects, clips };
}

describe('meshopt compression (npm run build)', () => {
  it('compresses geometry and keeps the JSON: nodes, extras, skins, instancing, clips, images', async () => {
    const original = fixtureGlb();
    const result = await compressGlb(original);
    expect(result.skipped).toBeUndefined();
    expect(result.views).toBeGreaterThanOrEqual(8);
    expect(result.after).toBeLessThan(result.before);
    expect(result.geometryAfter).toBeLessThan(result.geometryBefore);

    const before = readGlb(original)!.json;
    const after = readGlb(result.bytes)!.json;
    expect(after.extensionsRequired).toContain(MESHOPT);
    expect(after.extensionsUsed).toEqual([...before.extensionsUsed, MESHOPT]);
    for (const key of ['nodes', 'meshes', 'skins', 'animations', 'accessors', 'images', 'scenes']) {
      expect(after[key], key).toEqual(before[key]);
    }
    // Images and views another extension points at stay plain and keep their bytes.
    const plain = (json: typeof after, bin: Uint8Array, index: number) => {
      const v = json.bufferViews[index];
      return Array.from(bin.subarray(v.byteOffset, v.byteOffset + v.byteLength));
    };
    const imageView = before.images[0].bufferView;
    const extensionView = before.nodes[7].extensions.GS_test_extension.bufferView;
    for (const index of [imageView, extensionView]) {
      expect(after.bufferViews[index].extensions).toBeUndefined();
      expect(plain(after, readGlb(result.bytes)!.bin!, index)).toEqual(plain(before, readGlb(original)!.bin!, index));
    }
    expect(after.buffers[1]).toEqual({ byteLength: before.buffers[0].byteLength, extensions: { [MESHOPT]: { fallback: true } } });

    // Compressing again changes nothing.
    expect((await compressGlb(result.bytes)).skipped).toBe('already compressed');
  });

  it('loads in three.js exactly like the uncompressed file', async () => {
    const original = fixtureGlb();
    const { bytes } = await compressGlb(original);
    const [a, b] = [describe3d(await parse(original)), describe3d(await parse(bytes))];
    expect(b).toEqual(a);
    const names = (a.objects as { name: string }[]).map((o) => o.name);
    expect(names).toEqual(expect.arrayContaining(['Crate', 'ScatterBoxinstances', 'Blob', 'Body', 'Bone1']));
    expect((a.objects as { type: string }[]).some((o) => o.type === 'SkinnedMesh')).toBe(true);
    expect((a.objects as { instances?: number[] }[]).find((o) => o.instances)?.instances).toHaveLength(5 * 16);
    expect(a.clips[0]!.tracks.map((t) => t.name)).toEqual(['Crate.position', 'Bone1.quaternion', 'Blob.morphTargetInfluences']);
    const uv1 = (a.objects as { attributes?: Record<string, number[]> }[]).find((o) => o.attributes?.uv1);
    expect(uv1).toBeDefined();
  });

  it('compresses a Blender export (the basic fixture) losslessly', async () => {
    const original = new Uint8Array(readFileSync(FIXTURE));
    const { bytes, views } = await compressGlb(original);
    expect(views).toBeGreaterThan(0);
    expect(describe3d(await parse(bytes))).toEqual(describe3d(await parse(original)));
  });

  it('compresses an export folder in place and summarises it; geometry "none" leaves it', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'gs-compress-'));
    try {
      writeFileSync(path.join(dir, 'main.glb'), fixtureGlb());
      writeFileSync(path.join(dir, 'main.prefabs.glb'), readFileSync(FIXTURE));
      writeFileSync(path.join(dir, 'main.scene.json'), '{}');
      const untouched = await compressExports(dir, { geometry: 'none' });
      expect(untouched.map((r) => [r.file, r.before === r.after, r.skipped])).toEqual([
        ['main.glb', true, 'compression.geometry is "none"'],
        ['main.prefabs.glb', true, 'compression.geometry is "none"'],
      ]);
      expect(untouched[0]!.images).toEqual({ png: 1 });
      const results = await compressExports(dir, { geometry: 'meshopt' });
      expect(results.map((r) => r.file)).toEqual(['main.glb', 'main.prefabs.glb']);
      for (const r of results) expect(readFileSync(path.join(dir, r.file)).length).toBe(r.after);
      const lines = formatSummary(results, { geometry: 'meshopt', textures: 'none' });
      expect(lines[0]).toMatch(/geometry: meshopt/);
      expect(lines.some((l) => /main\.glb .* -> .*geometry/.test(l))).toBe(true);
      expect(lines.some((l) => /^\s+total/.test(l))).toBe(true);
      expect(lines.at(-1)).toMatch(/"textures": "webp"/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('reads the compression section of gamestudio.json', () => {
    const warnings: string[] = [];
    const logger = { warn: (m: string) => warnings.push(m) };
    expect(compressionConfig(undefined)).toEqual({ geometry: 'meshopt', textures: 'none' });
    expect(compressionConfig({ geometry: 'none', textures: 'webp' })).toEqual({ geometry: 'none', textures: 'webp' });
    expect(compressionConfig({ geometry: 'draco' }, logger)).toEqual({ geometry: 'meshopt', textures: 'none' });
    expect(warnings[0]).toMatch(/compression.geometry/);
  });
});
