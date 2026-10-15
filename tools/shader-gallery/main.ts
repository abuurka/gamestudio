// The shader gallery: one game shader on a preview mesh, lit the way a game is lit (sun with shadows,
// hemisphere fill tinted by the sky, studio environment, Neutral tone mapping, optional bloom and fog,
// ground), bound through the engine's own binding path (MaterialShaders: prepare, then bind, as a
// scene load does) and drawn at game time t. The preview mesh's MeshStandardMaterial is named
// GS_Preview. Every preview mesh has UVs as a GLB exported from Blender has them: V flipped (uv.y =
// 1 - V), so a standing plane's uv.y is 0 at its top.
//
//   ?shader=Water&mesh=plane&t=1.5&values={"speed":2}&camera=near&animate=1
//
// URL parameters (capture.mjs takes the same as --options; the shader's `preview` gives the defaults for
// mesh, camera, t, values, color, transparent, scale and occluder):
//   shader       the game shader; without it the page is an index of every shader
//   mesh         sphere | box | plane (8 m, flat) | cylinder | character (skinned, posed by t) | tree (two
//                crowns bound, trunk plain) | grass (2500 instanced blades, uv.y 1 at the root) | terrain
//                (200 m of gentle hills; the checker ground is hidden) | flag (an upright cloth on a pole,
//                pinned at uv.x 0) | building (the building kit's shell with no windows: two 3 m storeys of
//                hollow walls, a door, box-projected metre UVs) | gem (a round brilliant, flat facets)
//   t            game time in seconds (ctx.time)
//   values       field values as JSON, over the preview's
//   camera       near | far | top | eye (standing on the ground at 1.7 m, looking across: big surfaces)
//   scale        scales the preview mesh (and the framing, ground and shadows): plane with scale=25 is a 200 m sea
//   color        r,g,b or r,g,b,a: the preview material's base colour, linear (a < 1 also sets its opacity)
//   transparent  1 (0 to turn off the preview's): the material blends like Blender's Alpha Blend in a GLB
//                (transparent, no depth writes)
//   opacity      0..1, the material's opacity (with transparent=1)
//   fog          near,far or near,far,r,g,b: linear distance fog in metres (colour: the sky's horizon)
//   sky          day (default) | sunset | night | overcast (the sky kit's presets) | none (a flat background):
//                the background, the fill light's tint and the shaders' ctx.sky
//   occluder     1 (0 to turn off the preview's): a wall between the camera and the left half of the subject
//                (x-ray, hidden outlines)
//   bloom        1: the engine's bloom
//   animate      1: plays on from t (page view only)
//
// capture.mjs drives the page through `window.__gallery`.

import {
  Bone,
  BoxGeometry,
  CapsuleGeometry,
  Color,
  CylinderGeometry,
  DataTexture,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  Fog,
  Group,
  IcosahedronGeometry,
  InstancedMesh,
  LatheGeometry,
  LinearSRGBColorSpace,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  NearestFilter,
  NeutralToneMapping,
  PCFShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  PMREMGenerator,
  Quaternion,
  RepeatWrapping,
  Scene,
  Skeleton,
  SkinnedMesh,
  SphereGeometry,
  SRGBColorSpace,
  Uint16BufferAttribute,
  Vector2,
  Vector3,
  WebGLRenderer,
  type BufferGeometry,
  type Material,
  type Object3D,
  type Texture,
} from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { createFillLight } from '../../runtime/packages/engine/src/game/lighting';
import { PostService } from '../../runtime/packages/engine/src/game/post';
import type { GameRenderer } from '../../runtime/packages/engine/src/game/renderer';
import { createSkyUniforms, skyTexture, tintFillLight, updateSkyUniforms } from '../../runtime/packages/engine/src/game/sky';
import type { FogSettings, SkySettings } from '../../runtime/packages/engine/src/scene/manifest';
import { MaterialShaders } from '../../runtime/packages/engine/src/scene/materials';
import { getShader, listShaders, SHADER_CATEGORIES, type ShaderPreview } from '../../runtime/packages/engine/src/schema';

type MeshKind = NonNullable<ShaderPreview['mesh']>;
type CameraKind = NonNullable<ShaderPreview['camera']>;
type SkyKind = 'day' | 'sunset' | 'night' | 'overcast' | 'none';

export interface RenderOptions {
  shader: string;
  mesh?: MeshKind;
  t?: number;
  values?: Record<string, unknown>;
  camera?: CameraKind;
  bloom?: boolean;
  /** Linear RGB(A) base colour of the preview material (alpha < 1: its opacity). */
  color?: number[];
  /** Scales the preview mesh, its framing, the ground and the shadows. */
  scale?: number;
  /** Alpha Blend, as a GLB from Blender: transparent, no depth writes. */
  transparent?: boolean;
  opacity?: number;
  /** Linear distance fog: [near, far] or [near, far, r, g, b] (default colour: the sky's horizon). */
  fog?: number[];
  sky?: SkyKind;
  /** A wall between the camera and the left half of the subject. */
  occluder?: boolean;
  width?: number;
  height?: number;
}

export interface RenderResult {
  shader: string;
  mesh: MeshKind;
  camera: CameraKind;
  t: number;
  values: Record<string, unknown>;
  /** Shader compile and link errors (with the source lines around them). */
  compileErrors: string[];
  /** Engine and shader warnings (failed applies, ctx.warn, textures that did not load). */
  warnings: string[];
  /** Exceptions while setting up or drawing. */
  errors: string[];
  /** Milliseconds per frame with the shader, and with the plain material (same scene). */
  drawMs: number;
  baselineMs: number;
  png: string;
}

const MATERIAL = 'GS_Preview';
const MESHES: MeshKind[] = ['sphere', 'box', 'plane', 'cylinder', 'character', 'tree', 'grass', 'terrain', 'flag', 'building', 'gem'];
const CAMERAS: CameraKind[] = ['near', 'far', 'top', 'eye'];
const SKY_KINDS: SkyKind[] = ['day', 'sunset', 'night', 'overcast', 'none'];

// ---------------------------------------------------------------------------
// Renderer (configured like the engine's WebGL renderer, plus a drawing buffer we can read back)

const canvas = document.createElement('canvas');
document.body.append(canvas);
const raw = new WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
raw.outputColorSpace = SRGBColorSpace;
raw.toneMapping = NeutralToneMapping; // Khronos PBR Neutral: the templates' view transform
raw.toneMappingExposure = 1;
raw.shadowMap.enabled = true;
raw.shadowMap.type = PCFShadowMap;
raw.setPixelRatio(1);

let environment: Texture | null = null;
const renderer: GameRenderer = {
  kind: 'webgl',
  domElement: canvas,
  raw,
  setSize: (width, height) => raw.setSize(width, height, false),
  render: (scene, camera) => raw.render(scene, camera),
  createEnvironment() {
    const pmrem = new PMREMGenerator(raw);
    const room = new RoomEnvironment();
    const texture = pmrem.fromScene(room, 0.04).texture;
    room.dispose();
    pmrem.dispose();
    return texture;
  },
  dispose: () => raw.dispose(),
};

let compileErrors: string[] = [];
raw.debug.onShaderError = (gl, program, vertex, fragment) => {
  const parts: string[] = [];
  for (const [label, shader] of [['vertex', vertex], ['fragment', fragment]] as const) {
    if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) continue;
    const log = (gl.getShaderInfoLog(shader) ?? '').trim();
    parts.push(`${label} shader: ${log}\n${excerpt(gl.getShaderSource(shader) ?? '', log)}`);
  }
  if (!parts.length) parts.push(`link: ${(gl.getProgramInfoLog(program) ?? '').trim()}`);
  compileErrors.push(parts.join('\n'));
};

/** The source lines around the first error line of a compile log. */
function excerpt(source: string, log: string): string {
  const line = Number(/ERROR: \d+:(\d+)/.exec(log)?.[1] ?? 0);
  if (!line) return '';
  const lines = source.split('\n');
  const out: string[] = [];
  for (let i = Math.max(1, line - 3); i <= Math.min(lines.length, line + 3); i++) out.push(`${i === line ? '>' : ' '}${String(i).padStart(5)}: ${lines[i - 1]}`);
  return out.join('\n');
}

function gpuName(): string {
  const gl = raw.getContext();
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  return String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
}

// ---------------------------------------------------------------------------
// The stage

const linear = (r: number, g: number, b: number): Color => new Color().setRGB(r, g, b, LinearSRGBColorSpace);
const hex = (h: number): [number, number, number] => new Color().setHex(h).toArray() as [number, number, number]; // sRGB hex -> linear

/** The sky kit's presets (blender/gamestudio/kits/environment.py): horizon, zenith, ground. */
const SKIES: Record<Exclude<SkyKind, 'none'>, SkySettings> = {
  day: { horizon: hex(0xbfe3f2), zenith: hex(0x3c8ddb), ground: hex(0x8a9a88) },
  sunset: { horizon: hex(0xffb36b), zenith: hex(0x4a5fa8), ground: hex(0x5a4640) },
  night: { horizon: hex(0x1e2d4f), zenith: hex(0x05070f), ground: hex(0x0b0f18) },
  overcast: { horizon: hex(0xc9ced3), zenith: hex(0x8e99a6), ground: hex(0x6b6f70) },
};
/** The flat background of `sky=none`. */
const FLAT: [number, number, number] = [0.3, 0.36, 0.45];

/** 1 m ground checker: shows scale and world-space mapping. */
function checker(size: number): Texture {
  const a = 118;
  const b = 104;
  const texture = new DataTexture(new Uint8Array([a, a, a, 255, b, b, b, 255, b, b, b, 255, a, a, a, 255]), 2, 2);
  texture.colorSpace = SRGBColorSpace;
  texture.magFilter = NearestFilter;
  texture.wrapS = texture.wrapT = RepeatWrapping;
  texture.repeat.set(size / 2, size / 2);
  texture.needsUpdate = true;
  return texture;
}

interface Stage {
  scene: Scene;
  sun: DirectionalLight;
  ground: Mesh;
  /** What the game's applyEnvironment would get: the sky (or flat background) and fog. */
  settings: { sky: SkySettings | null; background: [number, number, number] | null; fog: FogSettings | null };
  dispose(): void;
}

function stage(sky: SkyKind, fog: number[] | undefined, extent: number): Stage {
  const scene = new Scene();
  const skySettings = sky === 'none' ? null : SKIES[sky];
  const background = skySettings ? skyTexture(skySettings) : null;
  scene.background = background ?? linear(...FLAT);
  environment ??= renderer.createEnvironment();
  scene.environment = environment;
  scene.environmentIntensity = 0.25; // the game's default
  const fill = createFillLight(0.3);
  tintFillLight(fill, skySettings); // as the game tints it
  scene.add(fill);
  let fogSettings: FogSettings | null = null;
  if (fog && fog.length >= 2) {
    const color = (fog.length >= 5 ? fog.slice(2, 5) : (skySettings?.horizon ?? FLAT)) as [number, number, number];
    fogSettings = { near: fog[0]!, far: fog[1]!, color };
    scene.fog = new Fog(linear(...color), fogSettings.near, fogSettings.far);
  }
  // The sun and its shadows cover the preview, whatever its size.
  const reach = Math.max(8, extent * 1.2);
  const k = reach / 8;
  const sun = new DirectionalLight(0xffffff, 2.5);
  sun.name = 'gs_default_sun';
  sun.position.set(5, 10, 7).multiplyScalar(k);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.02 * k;
  Object.assign(sun.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 0.5, far: 40 * k });
  scene.add(sun, sun.target);
  const size = Math.max(40, Math.ceil(extent * 6));
  const ground = new Mesh(new PlaneGeometry(size, size).rotateX(-Math.PI / 2), new MeshStandardMaterial({ name: 'Ground', map: checker(size), roughness: 0.92 }));
  ground.receiveShadow = true;
  scene.add(ground);
  return {
    scene,
    sun,
    ground,
    settings: { sky: skySettings, background: skySettings ? null : FLAT, fog: fogSettings },
    dispose: () => background?.dispose(),
  };
}

/** A simple deterministic random sequence (placement of grass blades). */
function seeded(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let x = Math.imul(s ^ (s >>> 15), 1 | s);
    x ^= x + Math.imul(x ^ (x >>> 7), 61 | x);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

interface Preview {
  group: Group;
  /** The meshes that draw the preview material. */
  meshes: Mesh[];
  focus: Vector3;
  radius: number;
  /** Half the size of the whole preview (the ground, the sun's shadows). */
  extent: number;
  /** Height of the preview's ground at (x, z), before scaling (0 but on the terrain). */
  heightAt(x: number, z: number): number;
  /** Hides the checker ground (the preview is the ground). */
  ownGround?: boolean;
  /** How far from the focus the `eye` camera stands, before scaling. */
  eyeDistance: number;
  /** Adjusts the preview to a scale (what must not grow with it). */
  scaled(scale: number): void;
  /** Poses the preview at game time t (the character's bones). */
  pose(t: number): void;
}

const BASE: Record<MeshKind, [number, number, number]> = {
  sphere: [0.6, 0.6, 0.6],
  box: [0.6, 0.6, 0.6],
  plane: [0.5, 0.55, 0.55],
  cylinder: [0.6, 0.6, 0.6],
  character: [0.35, 0.42, 0.62],
  tree: [0.16, 0.38, 0.1],
  grass: [0.22, 0.45, 0.12],
  terrain: [0.3, 0.33, 0.22],
  flag: [0.6, 0.08, 0.06],
  building: [0.62, 0.58, 0.52],
  gem: [0.6, 0.6, 0.6],
};

/** UVs as a GLB from Blender has them: glTF's V runs down (uv.y = 1 - V). Three's geometries have V up. */
function gltfUvs<G extends BufferGeometry>(geometry: G): G {
  const uv = geometry.getAttribute('uv');
  if (uv) {
    for (let i = 0; i < uv.count; i++) uv.setY(i, 1 - uv.getY(i));
    uv.needsUpdate = true;
  }
  return geometry;
}

/** A skinned capsule with three bones along +Y. */
function character(material: Material): { mesh: SkinnedMesh; bones: Bone[] } {
  const geometry: BufferGeometry = gltfUvs(new CapsuleGeometry(0.35, 1.1, 8, 24).translate(0, 0.9, 0));
  const position = geometry.getAttribute('position');
  const indices: number[] = [];
  const weights: number[] = [];
  for (let i = 0; i < position.count; i++) {
    const y = position.getY(i);
    const w = Math.min(Math.max((y - 0.5) / 0.8, 0), 1); // 0 below the hips, 1 at the chest
    const h = Math.min(Math.max((y - 1.3) / 0.3, 0), 1); // the head
    indices.push(0, 1, 2, 0);
    weights.push(1 - w, w * (1 - h), w * h, 0);
  }
  geometry.setAttribute('skinIndex', new Uint16BufferAttribute(indices, 4));
  geometry.setAttribute('skinWeight', new Float32BufferAttribute(weights, 4));
  const hips = new Bone();
  const spine = new Bone();
  const head = new Bone();
  spine.position.y = 0.9;
  head.position.y = 0.55;
  hips.add(spine);
  spine.add(head);
  const mesh = new SkinnedMesh(geometry, material);
  mesh.add(hips);
  mesh.bind(new Skeleton([hips, spine, head]));
  return { mesh, bones: [hips, spine, head] };
}

/** Gentle hills: about 0 to 9 m over 200 m. */
function hills(x: number, z: number): number {
  return (
    3 +
    3.2 * Math.sin(x / 27 + 0.4) * Math.cos(z / 33 - 0.2) +
    1.6 * Math.sin((x + z) / 17 + 1.3) +
    0.6 * Math.cos((0.8 * x - z) / 9.5) +
    0.25 * Math.sin(x / 4.1 + 2.0) * Math.sin(z / 3.7)
  );
}

/** 200 m of hills, 256 × 256 quads, a Blender grid's UVs (0..1 over the whole). */
function terrain(): BufferGeometry {
  const geometry = gltfUvs(new PlaneGeometry(200, 200, 256, 256).rotateX(-Math.PI / 2));
  const position = geometry.getAttribute('position');
  for (let i = 0; i < position.count; i++) position.setY(i, hills(position.getX(i), position.getZ(i)));
  geometry.computeVertexNormals();
  return geometry;
}

/**
 * Box-projected metre UVs as the building kit makes them (blender/gamestudio/kits/geometry.py
 * `box_uv`), in Blender's axes (X, Y = -z, Z = y), then V flipped like the glTF exporter. The
 * geometry must be non-indexed with face normals.
 */
function boxUvs(geometry: BufferGeometry): BufferGeometry {
  const position = geometry.getAttribute('position');
  const normal = geometry.getAttribute('normal');
  const uvs = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    const [x, y, z] = [position.getX(i), -position.getZ(i), position.getY(i)];
    const [nx, ny, nz] = [normal.getX(i), -normal.getZ(i), normal.getY(i)];
    const axis = Math.abs(nx) >= Math.abs(ny) && Math.abs(nx) >= Math.abs(nz) ? 0 : Math.abs(ny) >= Math.abs(nz) ? 1 : 2;
    const [u, v] = axis === 0 ? [nx > 0 ? y : -y, z] : axis === 1 ? [ny > 0 ? -x : x, z] : [x, nz > 0 ? y : -y];
    uvs[i * 2] = u;
    uvs[i * 2 + 1] = 1 - v;
  }
  geometry.setAttribute('uv', new Float32BufferAttribute(uvs, 2));
  return geometry;
}

/** A wall slab along +X from 0 to `length`, `thickness` deep toward -Z, with rectangular openings [u0, u1, y0, y1]. */
function wallPieces(length: number, height: number, thickness: number, openings: number[][]): BufferGeometry[] {
  const lines = (values: number[]): number[] => [...new Set(values.map((v) => Math.round(v * 1000) / 1000))].sort((a, b) => a - b);
  const us = lines([0, length, ...openings.flatMap((o) => [o[0]!, o[1]!])]);
  const ys = lines([0, height, ...openings.flatMap((o) => [o[2]!, o[3]!])]);
  const pieces: BufferGeometry[] = [];
  for (let i = 0; i + 1 < us.length; i++) {
    for (let j = 0; j + 1 < ys.length; j++) {
      const cu = (us[i]! + us[i + 1]!) / 2;
      const cy = (ys[j]! + ys[j + 1]!) / 2;
      if (openings.some(([u0, u1, y0, y1]) => u0! < cu && cu < u1! && y0! < cy && cy < y1!)) continue;
      const w = us[i + 1]! - us[i]!;
      const h = ys[j + 1]! - ys[j]!;
      pieces.push(new BoxGeometry(w, h, thickness).translate(cu, cy, -thickness / 2));
    }
  }
  return pieces;
}

/**
 * The building kit's shell with `windows: 0` (as InteriorMapping wants it): two storeys of hollow
 * walls with a door, drawing `walls`; floors and a flat roof.
 */
function building(walls: Material, plain: Material): { walls: Mesh; rest: Mesh[] } {
  const [W, D, fh, storeys, t] = [6, 5, 3, 2, 0.25];
  const H = fh * storeys;
  const door = [W / 2 - 0.6, W / 2 + 0.6, 0, 2.2];
  // Each wall starts at its left end seen from outside; its outer face is local z = 0, inside -z.
  const sides: Array<{ length: number; openings: number[][]; matrix: Matrix4 }> = [
    { length: W, openings: [door], matrix: new Matrix4().makeTranslation(-W / 2, 0, D / 2) },
    { length: W, openings: [], matrix: new Matrix4().makeTranslation(W / 2, 0, -D / 2).multiply(new Matrix4().makeRotationY(Math.PI)) },
    { length: D - 2 * t, openings: [], matrix: new Matrix4().makeTranslation(-W / 2, 0, -D / 2 + t).multiply(new Matrix4().makeRotationY(-Math.PI / 2)) },
    { length: D - 2 * t, openings: [], matrix: new Matrix4().makeTranslation(W / 2, 0, D / 2 - t).multiply(new Matrix4().makeRotationY(Math.PI / 2)) },
  ];
  const pieces = sides.flatMap(({ length, openings, matrix }) => wallPieces(length, H, t, openings).map((g) => g.applyMatrix4(matrix)));
  const shell = boxUvs(mergeGeometries(pieces.map((g) => g.toNonIndexed()))!);
  const rest: Mesh[] = [];
  for (let s = 0; s < storeys; s++) {
    const floor = new Mesh(boxUvs(new BoxGeometry(W - 2 * t, 0.12, D - 2 * t).toNonIndexed()), plain);
    floor.position.y = s * fh + 0.06;
    rest.push(floor);
  }
  const roof = new Mesh(boxUvs(new BoxGeometry(W + 0.4, 0.25, D + 0.4).toNonIndexed()), plain);
  roof.position.y = H + 0.125;
  rest.push(roof);
  return { walls: new Mesh(shell, walls), rest };
}

/**
 * A round brilliant (about 1.2 m across): table, crown, girdle and pavilion with flat facets, twelve
 * around, as a gem modelled in Blender and shaded flat exports.
 */
function gem(): BufferGeometry {
  const profile = [
    new Vector2(0.001, -0.53), // culet
    new Vector2(0.36, -0.28), // lower girdle facets
    new Vector2(0.62, 0),
    new Vector2(0.62, 0.04), // girdle
    new Vector2(0.5, 0.14), // bezel
    new Vector2(0.35, 0.22), // table edge
    new Vector2(0.001, 0.22),
  ];
  const geometry = gltfUvs(new LatheGeometry(profile, 12)).toNonIndexed();
  geometry.computeVertexNormals(); // non-indexed: one normal per facet
  return geometry;
}

function preview(kind: MeshKind, material: MeshStandardMaterial): Preview {
  const group = new Group();
  const meshes: Mesh[] = [];
  let pose = (_t: number): void => {};
  let heightAt = (_x: number, _z: number): number => 0;
  let ownGround = false;
  let eyeDistance = 0;
  let scaled = (_scale: number): void => {};
  const add = (mesh: Mesh): Mesh => {
    group.add(mesh);
    meshes.push(mesh);
    return mesh;
  };
  const plain = (name: string, color: Color, roughness = 0.85): MeshStandardMaterial => new MeshStandardMaterial({ name, color, roughness });
  let focus = new Vector3(0, 1, 0);
  let radius = 1.2;
  let extent = 0;
  switch (kind) {
    case 'sphere':
      add(new Mesh(gltfUvs(new SphereGeometry(1, 96, 48)), material)).position.y = 1;
      break;
    case 'box': {
      const box = add(new Mesh(gltfUvs(new BoxGeometry(1.6, 1.6, 1.6, 8, 8, 8)), material));
      box.position.y = 0.8;
      box.rotation.y = 0.5;
      focus = new Vector3(0, 0.8, 0);
      radius = 1.4;
      break;
    }
    case 'plane': {
      const plane = add(new Mesh(gltfUvs(new PlaneGeometry(8, 8, 160, 160).rotateX(-Math.PI / 2)), material));
      scaled = (s) => void (plane.position.y = 0.05 / s); // 5 cm above the ground at any scale
      focus = new Vector3(0, 0, 0);
      radius = 3.2;
      extent = 4;
      eyeDistance = 3.6; // at its near edge, looking across
      break;
    }
    case 'cylinder':
      add(new Mesh(gltfUvs(new CylinderGeometry(0.6, 0.6, 2.4, 64, 24)), material)).position.y = 1.2;
      focus = new Vector3(0, 1.2, 0);
      radius = 1.4;
      break;
    case 'character': {
      const { mesh, bones } = character(material);
      add(mesh);
      pose = (t) => {
        bones[1]!.rotation.set(0.15 * Math.sin(t * 2), 0.4 * Math.sin(t), 0.2 * Math.sin(t * 1.3));
        bones[2]!.rotation.set(0.2 * Math.sin(t * 1.7), -0.3 * Math.sin(t), 0);
      };
      focus = new Vector3(0, 0.95, 0);
      radius = 1.05;
      break;
    }
    case 'tree': {
      const trunk = new Mesh(gltfUvs(new CylinderGeometry(0.14, 0.22, 1.8, 16)), plain('Bark', linear(0.12, 0.07, 0.04), 0.9));
      trunk.position.y = 0.9;
      group.add(trunk);
      const crown = add(new Mesh(gltfUvs(new IcosahedronGeometry(1.1, 3)), material));
      crown.position.set(0, 2.4, 0);
      crown.scale.set(1, 0.85, 1);
      const side = add(new Mesh(gltfUvs(new IcosahedronGeometry(0.7, 3)), material));
      side.position.set(0.75, 1.9, 0.3);
      focus = new Vector3(0, 1.8, 0);
      radius = 2;
      break;
    }
    case 'grass': {
      material.side = DoubleSide;
      const blade = gltfUvs(new PlaneGeometry(0.05, 0.55, 1, 5).translate(0, 0.275, 0));
      const count = 2500;
      const grass = add(new InstancedMesh(blade, material, count));
      const random = seeded(7);
      const m = new Matrix4();
      const q = new Quaternion();
      for (let i = 0; i < count; i++) {
        const r = Math.sqrt(random()) * 1.6;
        const a = random() * Math.PI * 2;
        q.setFromAxisAngle(new Vector3(0, 1, 0), random() * Math.PI);
        const s = 0.7 + random() * 0.6;
        m.compose(new Vector3(Math.cos(a) * r, 0, Math.sin(a) * r), q, new Vector3(1, s, 1));
        (grass as InstancedMesh).setMatrixAt(i, m);
      }
      grass.frustumCulled = false;
      focus = new Vector3(0, 0.3, 0);
      radius = 1.7;
      break;
    }
    case 'terrain':
      add(new Mesh(terrain(), material));
      heightAt = hills;
      ownGround = true;
      focus = new Vector3(0, hills(0, 0), 0);
      radius = 45;
      extent = 100;
      eyeDistance = 90;
      break;
    case 'flag': {
      material.side = DoubleSide; // a cloth: Blender's backface culling off
      const pole = new Mesh(gltfUvs(new CylinderGeometry(0.035, 0.045, 3.3, 16)), plain('Pole', linear(0.25, 0.25, 0.27), 0.5));
      pole.position.y = 1.65;
      group.add(pole);
      // Pinned along its left edge (uv.x 0) at the pole; uv.y 0 at the top, as in a GLB.
      add(new Mesh(gltfUvs(new PlaneGeometry(1.6, 1, 48, 30)), material)).position.set(0.84, 2.65, 0);
      focus = new Vector3(0.6, 2.2, 0);
      radius = 1.3;
      break;
    }
    case 'building': {
      const { walls, rest } = building(material, plain('Floor', linear(0.3, 0.27, 0.24)));
      add(walls);
      group.add(...rest);
      focus = new Vector3(0, 3, 0);
      radius = 4.6;
      extent = 4;
      break;
    }
    case 'gem': {
      const stone = add(new Mesh(gem(), material));
      stone.position.y = 0.9;
      stone.rotation.set(0.45, 0, -0.25); // the table turned toward the camera
      focus = new Vector3(0, 0.9, 0);
      radius = 0.72;
      break;
    }
  }
  group.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh) return;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  });
  return { group, meshes, focus, radius, extent: Math.max(extent, radius), heightAt, ownGround, eyeDistance: eyeDistance || Math.max(radius * 1.6, 3), scaled, pose };
}

function place(camera: PerspectiveCamera, view: CameraKind, focus: Vector3, radius: number, eye: { distance: number; groundAt(x: number, z: number): number }): void {
  const distance = radius / Math.sin(((camera.fov / 2) * Math.PI) / 180);
  if (view === 'top') {
    camera.position.copy(focus).add(new Vector3(0, distance * 1.15, 0.001));
  } else if (view === 'eye') {
    // Standing on the ground in front of the subject, eyes 1.7 m up.
    const across = new Vector3(0.55, 0, 1).normalize().multiplyScalar(eye.distance);
    const x = focus.x + across.x;
    const z = focus.z + across.z;
    camera.position.set(x, eye.groundAt(x, z) + 1.7, z);
  } else {
    const direction = new Vector3(0.55, 0.42, 1).normalize();
    camera.position.copy(focus).addScaledVector(direction, distance * (view === 'far' ? 2.6 : 1.08));
  }
  camera.far = Math.max(200, camera.position.distanceTo(focus) + radius * 6);
  camera.updateProjectionMatrix();
  camera.lookAt(focus);
  camera.updateMatrixWorld();
}

/** A plain wall between the camera and the left half of the subject (a slab above it, seen from the top). */
function occluder(camera: PerspectiveCamera, focus: Vector3, radius: number): Mesh {
  const material = new MeshStandardMaterial({ name: 'Wall', color: linear(0.42, 0.4, 0.37), roughness: 0.85 });
  const toCamera = camera.position.clone().sub(focus);
  const flat = new Vector3(toCamera.x, 0, toCamera.z);
  let wall: Mesh;
  if (flat.lengthSq() < 0.01 * toCamera.lengthSq()) {
    wall = new Mesh(new BoxGeometry(radius * 1.6, radius * 0.08, radius * 3.2), material);
    wall.position.set(focus.x - radius * 0.8, focus.y + radius * 1.3, focus.z);
  } else {
    flat.normalize();
    const right = flat.clone().negate().cross(new Vector3(0, 1, 0)).normalize();
    const width = radius * 1.6;
    const height = Math.max(focus.y + radius * 1.5, radius * 2);
    wall = new Mesh(new BoxGeometry(width, height, radius * 0.08), material);
    wall.position.copy(focus).addScaledVector(flat, radius * 1.35).addScaledVector(right, -width / 2);
    wall.position.y = height / 2;
    wall.rotation.y = Math.atan2(flat.x, flat.z);
  }
  wall.name = 'Occluder';
  wall.receiveShadow = true;
  return wall;
}

// ---------------------------------------------------------------------------
// Rendering

let last: { scene: Scene; shaders: MaterialShaders; stage: Stage } | null = null;

function dispose(object: Object3D): void {
  object.traverse((o) => {
    const mesh = o as Mesh;
    mesh.geometry?.dispose();
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    for (const material of materials) material.dispose();
  });
}

/**
 * Milliseconds per frame, waiting for the GPU (a 1-pixel read): the best of a few batches after a
 * warm-up (the first frames compile programs and wake the GPU's clocks).
 */
function frameTime(draw: () => void, frames = 10, batches = 4): number {
  const gl = raw.getContext();
  const pixel = new Uint8Array(4);
  const sync = (): void => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
  for (let i = 0; i < 6; i++) draw();
  sync();
  let best = Infinity;
  for (let b = 0; b < batches; b++) {
    const start = performance.now();
    for (let i = 0; i < frames; i++) draw();
    sync();
    best = Math.min(best, (performance.now() - start) / frames);
  }
  return best;
}

async function render(options: RenderOptions): Promise<RenderResult & { animate(): void }> {
  const shader = getShader(options.shader);
  const kind = MESHES.includes(options.mesh as MeshKind) ? options.mesh! : (shader?.preview?.mesh ?? 'sphere');
  const view = CAMERAS.includes(options.camera as CameraKind) ? options.camera! : (shader?.preview?.camera ?? 'near');
  const t = options.t ?? shader?.preview?.t ?? 1;
  const values = { ...(shader?.preview?.values ?? {}), ...(options.values ?? {}) } as Record<string, unknown>;
  const shown = shader?.preview ?? {};
  const result: RenderResult = { shader: options.shader, mesh: kind, camera: view, t, values, compileErrors: [], warnings: [], errors: [], drawMs: 0, baselineMs: 0, png: '' };
  compileErrors = [];
  if (last) {
    dispose(last.scene);
    last.stage.dispose();
    last.shaders.clear();
    last = null;
  }
  const width = options.width ?? window.innerWidth;
  const height = options.height ?? window.innerHeight;
  raw.setSize(width, height, false);
  const camera = new PerspectiveCamera(40, width / height, 0.05, 200);

  const color = options.color?.length ? options.color : (shader?.preview?.color ?? BASE[kind]);
  const material = new MeshStandardMaterial({ name: MATERIAL, color: linear(color[0]!, color[1]!, color[2]!), roughness: 0.55 });
  const opacity = options.opacity ?? (color.length > 3 && color[3]! < 1 ? color[3]! : 1);
  if ((options.transparent ?? shown.transparent) || opacity < 1) {
    material.transparent = true; // a GLB's Alpha Blend
    material.depthWrite = false;
  }
  material.opacity = opacity;
  const view3 = preview(kind, material);
  const scale = [options.scale, shown.scale].find((s): s is number => typeof s === 'number' && s > 0) ?? 1;
  view3.group.scale.setScalar(scale);
  view3.scaled(scale);
  const focus = view3.focus.clone().multiplyScalar(scale);
  const radius = view3.radius * scale;
  const set = stage(SKY_KINDS.includes(options.sky as SkyKind) ? options.sky! : 'day', options.fog, view3.extent * scale);
  const scene = set.scene;
  set.ground.visible = !view3.ownGround;
  scene.add(view3.group);
  place(camera, view, focus, radius, { distance: view3.eyeDistance * scale, groundAt: (x, z) => view3.heightAt(x / scale, z / scale) * scale });
  if (options.occluder ?? shown.occluder) scene.add(occluder(camera, focus, radius));

  const time = { value: t };
  const sky = createSkyUniforms();
  updateSkyUniforms(sky, set.settings); // as the game does on a scene load
  const shaders = new MaterialShaders({
    renderer: () => renderer,
    time,
    sky,
    assetUrl: (path) => (/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(path) ? path : new URL(path, location.href).href),
    warn: (message) => {
      result.warnings.push(message);
      console.warn(`[gamestudio] ${message}`);
    },
  });
  last = { scene, shaders, stage: set };
  const post = options.bloom ? new PostService({ bloom: true }) : null;
  const draw = (): void => {
    if (!post?.render(renderer, scene, camera)) raw.render(scene, camera);
  };
  try {
    view3.pose(t);
    result.baselineMs = frameTime(draw); // the plain material, same scene
    if (!shader) throw new Error(`No game shader "${options.shader}"`);
    const materials = { [MATERIAL]: { shader: options.shader, values } };
    await shaders.prepare(materials);
    shaders.bind([view3.group], materials, () => [scene]);
    raw.compile(scene, camera);
    result.drawMs = frameTime(draw);
    draw();
    result.png = canvas.toDataURL('image/png');
  } catch (error) {
    result.errors.push(error instanceof Error ? (error.stack ?? error.message) : String(error));
  }
  result.compileErrors = [...compileErrors];
  return {
    ...result,
    /** Plays on from t (the page view's `animate=1`). */
    animate() {
      const start = performance.now();
      const tick = (): void => {
        if (last?.scene !== scene) return;
        time.value = t + (performance.now() - start) / 1000;
        view3.pose(time.value);
        draw();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    },
  };
}

/** One contact sheet of captures (PNG data URLs), labelled; a failed one gets a red label. */
async function sheet(items: Array<{ name: string; png: string; ok: boolean; note?: string }>, options: { columns?: number; cell?: [number, number] } = {}): Promise<string> {
  const columns = options.columns ?? 6;
  const [w, h] = options.cell ?? [320, 240];
  const label = 22;
  const rows = Math.ceil(items.length / columns);
  const out = document.createElement('canvas');
  out.width = columns * w;
  out.height = rows * (h + label);
  const c = out.getContext('2d')!;
  c.fillStyle = '#15171c';
  c.fillRect(0, 0, out.width, out.height);
  c.font = '13px system-ui, sans-serif';
  for (const [i, item] of items.entries()) {
    const x = (i % columns) * w;
    const y = Math.floor(i / columns) * (h + label);
    if (item.png) {
      const image = new Image();
      image.src = item.png;
      await image.decode();
      c.drawImage(image, x, y + label, w, h);
    }
    c.fillStyle = item.ok ? '#d8dbe2' : '#ff6b6b';
    c.fillText(`${item.name}${item.note ? `  ${item.note}` : ''}`, x + 6, y + 15);
  }
  return out.toDataURL('image/png');
}

function list(): Array<{ name: string; category?: string; builtin: boolean; preview?: ShaderPreview }> {
  return listShaders().map((s) => ({ name: s.name, category: s.category, builtin: s.builtin, preview: s.preview }));
}

declare global {
  interface Window {
    __gallery: {
      info(): { gpu: string; meshes: MeshKind[]; cameras: CameraKind[]; skies: SkyKind[] };
      list: typeof list;
      render(options: RenderOptions): Promise<RenderResult>;
      sheet: typeof sheet;
    };
  }
}

window.__gallery = {
  info: () => ({ gpu: gpuName(), meshes: MESHES, cameras: CAMERAS, skies: SKY_KINDS }),
  list,
  render: async (options) => {
    const { animate: _animate, ...result } = await render(options);
    return result;
  },
  sheet,
};

// ---------------------------------------------------------------------------
// The page itself

const params = new URLSearchParams(location.search);
const name = params.get('shader');
const numbers = (key: string): number[] | undefined => params.get(key)?.split(',').map(Number).filter((n) => Number.isFinite(n));
const number = (key: string): number | undefined => (params.has(key) ? Number(params.get(key)) : undefined);
if (name) {
  const values = params.get('values');
  void render({
    shader: name,
    mesh: (params.get('mesh') as MeshKind | null) ?? undefined,
    camera: (params.get('camera') as CameraKind | null) ?? undefined,
    t: number('t'),
    values: values ? (JSON.parse(values) as Record<string, unknown>) : undefined,
    bloom: params.get('bloom') === '1',
    color: numbers('color'),
    scale: number('scale'),
    transparent: params.has('transparent') ? params.get('transparent') === '1' : undefined,
    opacity: number('opacity'),
    fog: numbers('fog'),
    sky: (params.get('sky') as SkyKind | null) ?? undefined,
    occluder: params.has('occluder') ? params.get('occluder') === '1' : undefined,
  }).then((result) => {
    const info = document.createElement('div');
    info.id = 'info';
    const problems = [...result.compileErrors, ...result.warnings, ...result.errors];
    info.textContent = `${result.shader} on ${result.mesh}, t=${result.t}s, ${result.drawMs.toFixed(2)} ms/frame (plain ${result.baselineMs.toFixed(2)})${problems.length ? `\n${problems.join('\n')}` : ''}`;
    document.body.append(info);
    if (params.get('animate') === '1') result.animate();
  });
} else {
  canvas.style.display = 'none';
  const index = document.getElementById('index')!;
  index.style.display = 'block';
  const shaders = list();
  const groups = [...SHADER_CATEGORIES, undefined];
  let html = '<h1>Game shaders</h1>';
  for (const category of groups) {
    const members = shaders.filter((s) => (category ? s.category === category : !SHADER_CATEGORIES.includes(s.category as never)));
    if (!members.length) continue;
    html += `<h2>${category ?? 'Other'}</h2>` + members.map((s) => `<a href="?shader=${s.name}">${s.name}</a>`).join('');
  }
  index.innerHTML = html;
}
