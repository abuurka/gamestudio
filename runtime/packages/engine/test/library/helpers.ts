// Test helper for library shaders (and any game shader): binds a shader to a preview mesh through the
// engine's own binding path (MaterialShaders: prepare + bind, as a scene load does), runs the
// material's onBeforeCompile chain against three's real ShaderLib sources, and FAILS when the patch
// is broken in a way WebGL would not tell you about:
//
// - a `replace()` anchor that did not match (a silent no-op, the #1 porting bug). How: while the
//   onBeforeCompile chain runs, String.prototype.replace and replaceAll are wrapped; every call
//   whose subject is multi-line text (shader source or a GLSL snippet) must find its pattern. A
//   patch that replaces something optional on purpose names the pattern in `allowMiss`.
// - a uniform the patch declares in GLSL but never puts in `shader.uniforms` (it would read 0).
//   A replacement material (a ShaderMaterial returned from apply): every uniform its GLSL declares
//   (includes expanded, `#ifdef`s resolved as three.js would for this mesh in a game scene with a
//   sun, a fill light, shadows and fog) must be in its `uniforms` or set by three.js itself.
// - a uniform the patch (or the replacement) puts in the uniforms and uses in a stage that does
//   not declare it (a compile error the gallery would show).
// - a uniform or varying the patch declares twice (a compile error).
// - an apply or mesh hook that throws (the engine's "failed" warning).
//
// It cannot compile GLSL (no WebGL in Node): the gallery's capture does that
// (node tools/shader-gallery/capture.mjs <Shader>).

import {
  BoxGeometry,
  Bone,
  BufferGeometry,
  CylinderGeometry,
  DataTexture,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  IcosahedronGeometry,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  PlaneGeometry,
  Scene,
  ShaderChunk,
  ShaderLib,
  Skeleton,
  SkinnedMesh,
  SphereGeometry,
  Uint16BufferAttribute,
  UniformsUtils,
  type Material,
  type ShaderMaterial,
  type Texture,
} from 'three';
import { World } from '../../src/ecs/world';
import type { Entity } from '../../src/ecs/entity';
import { createSkyUniforms } from '../../src/game/sky';
import { MaterialShaders } from '../../src/scene/materials';
import { getShader, type ShaderPreview, type ShaderSky } from '../../src/schema';

export type PreviewMesh = NonNullable<ShaderPreview['mesh']>;

export interface CompileOptions {
  /** Geometry of the preview mesh (default `'box'`). */
  mesh?: PreviewMesh;
  /** Field values over the defaults (as a manifest's `values`). */
  values?: Record<string, unknown>;
  /** A SkinnedMesh with a two-bone skeleton. */
  skinned?: boolean;
  /** An InstancedMesh with four instances (GPU instancing, as geometry-nodes scatters export). */
  instanced?: boolean;
  /** The material to bind instead of a grey MeshStandardMaterial; its name is the material name. */
  material?: Material;
  /** `replace()` patterns that may miss on purpose (optional patches). */
  allowMiss?: Array<string | RegExp>;
  /** The shared time uniform's value. */
  time?: number;
}

/** One `replace()`/`replaceAll()` call made by the onBeforeCompile chain on multi-line text. */
export interface ShaderEditCall {
  pattern: string;
  matched: boolean;
}

export interface CompiledMaterial {
  /** The sources after onBeforeCompile (`#include`s not expanded; see `resolveIncludes`). */
  vertexShader: string;
  fragmentShader: string;
  /** `shader.uniforms` after onBeforeCompile: three's own plus the patch's (the same objects as `ctx.uniforms`). */
  uniforms: Record<string, { value: any }>;
  defines: Record<string, unknown>;
  /** Every checked replace call, in order. */
  edits: ShaderEditCall[];
}

export interface PatchedShader extends CompiledMaterial {
  /** The material the preview mesh draws after binding (the shader's result). */
  material: Material;
  /** The material as loaded, before binding (never changed). */
  original: Material;
  /** A Mesh, an InstancedMesh (`instanced`) or a SkinnedMesh (`skinned`). */
  mesh: Mesh & { readonly isInstancedMesh?: true; readonly isSkinnedMesh?: true };
  /** The preview mesh's entity, for `shaders.set(entity, ...)`. */
  entity: Entity;
  world: World;
  /** The binder: `shaders.set('GS_Preview', {...})`, `shaders.set(entity, {...})`, `shaders.get(...)`. */
  shaders: MaterialShaders;
  /** The shared time uniform. */
  time: { value: number };
  /** The shared sky uniforms (`ctx.sky`). */
  sky: ShaderSky;
  /** Warnings the engine and the shader gave (ctx.warn). */
  warnings: string[];
  /** Compiles another material the same way (e.g. the entity's own copy after `shaders.set(entity, ...)`). */
  compile(material?: Material): CompiledMaterial;
}

/** The material name the helper binds. */
export const PREVIEW_MATERIAL = 'GS_Preview';

/**
 * Uniforms three.js sets itself, whatever the material's `uniforms` hold (the program prefix, the
 * renderer's setProgram: skinning, morphs, batching, multi-draw, clipping, shadow maps).
 */
export const PROVIDED_UNIFORMS: ReadonlySet<string> = new Set([
  'modelMatrix', 'modelViewMatrix', 'projectionMatrix', 'viewMatrix', 'normalMatrix', 'cameraPosition', 'isOrthographic',
  'bindMatrix', 'bindMatrixInverse', 'boneTexture', 'morphTargetBaseInfluence', 'morphTargetInfluences', 'morphTargetsTexture',
  'morphTargetsTextureSize', 'logDepthBufFC', 'toneMappingExposure', 'clippingPlanes', 'batchingTexture', 'batchingIdTexture',
  'batchingColorTexture', '_gl_DrawID', 'receiveShadow', 'directionalShadowMap', 'spotShadowMap', 'pointShadowMap', 'sunShadowMap',
]);
const PROVIDED = PROVIDED_UNIFORMS;

/** Expands `#include <chunk>` like three.js does (recursively). */
export function resolveIncludes(source: string): string {
  return source.replace(/^[ \t]*#include +<([\w\d./]+)>/gm, (_all, name: string) => {
    const chunk = (ShaderChunk as Record<string, string>)[name];
    if (chunk === undefined) throw new Error(`Can not resolve #include <${name}>`);
    return resolveIncludes(chunk);
  });
}

/** three's ShaderLib entry for a built-in material type. */
function libraryFor(material: Material): { vertexShader: string; fragmentShader: string; uniforms: Record<string, { value: unknown }> } {
  const m = material as ShaderMaterial;
  if (m.isShaderMaterial) return { vertexShader: m.vertexShader, fragmentShader: m.fragmentShader, uniforms: m.uniforms };
  const byType: Record<string, keyof typeof ShaderLib> = {
    MeshStandardMaterial: 'standard',
    MeshPhysicalMaterial: 'physical',
    MeshBasicMaterial: 'basic',
    MeshLambertMaterial: 'lambert',
    MeshPhongMaterial: 'phong',
    MeshToonMaterial: 'toon',
    MeshMatcapMaterial: 'matcap',
    MeshNormalMaterial: 'normal',
    MeshDepthMaterial: 'depth',
    MeshDistanceMaterial: 'distance',
    PointsMaterial: 'points',
    LineBasicMaterial: 'basic',
    LineDashedMaterial: 'dashed',
    ShadowMaterial: 'shadow',
    SpriteMaterial: 'sprite',
  };
  const name = byType[material.type];
  if (!name) throw new Error(`compilePatched: no ShaderLib entry for ${material.type}`);
  const lib = ShaderLib[name];
  return { vertexShader: lib.vertexShader, fragmentShader: lib.fragmentShader, uniforms: UniformsUtils.clone(lib.uniforms) };
}

/** The names a source declares as `kind` (`uniform vec3 a, b[2];` declares a and b). */
const declared = (source: string, kind: 'uniform' | 'varying'): string[] => {
  const names: string[] = [];
  const pattern = new RegExp(`^\\s*(?:flat\\s+)?${kind}\\s+(?:(?:lowp|mediump|highp)\\s+)?\\w+\\s+(\\w+(?:\\s*\\[[^\\]]*\\])?(?:\\s*,\\s*\\w+(?:\\s*\\[[^\\]]*\\])?)*)\\s*;`, 'gm');
  for (const match of source.matchAll(pattern)) {
    for (const part of match[1]!.split(',')) names.push(part.trim().replace(/\s*\[.*$/, ''));
  }
  return names;
};

const withoutComments = (source: string): string => source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

/** Whether a C-preprocessor `#if` expression holds; true when it cannot be evaluated (checks then see more, not less). */
function holds(expression: string, env: ReadonlyMap<string, string>): boolean {
  let e = withoutComments(expression).replace(/defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)/g, (_all, a?: string, b?: string) => (env.has((a ?? b)!) ? '1' : '0'));
  for (let i = 0; i < 4 && /[A-Za-z_]/.test(e); i++) {
    e = e.replace(/\b[A-Za-z_]\w*\b/g, (name) => {
      const value = env.get(name);
      return value === undefined ? '0' : value.trim() === '' ? '1' : `(${value})`;
    });
  }
  if (!/^[\d\s()+\-*/%<>=!&|~^?:]*$/.test(e) || !e.trim()) return true;
  try {
    return !!new Function(`return (${e});`)();
  } catch {
    return true;
  }
}

/**
 * The source with `#include`s expanded and only the lines `defines` leave active: a small C
 * preprocessor (`#if`, `#ifdef`, `#ifndef`, `#elif`, `#else`, `#endif`, `#define`, `#undef`), with
 * three.js's light counts replaced as it does.
 */
export function activeGlsl(source: string, defines: Readonly<Record<string, string>> = {}): string {
  const env = new Map(Object.entries(defines));
  const out: string[] = [];
  const stack: Array<{ active: boolean; taken: boolean; outer: boolean }> = [];
  for (const line of resolveIncludes(source).split('\n')) {
    const active = stack.length ? stack[stack.length - 1]!.active : true;
    const directive = /^\s*#\s*(\w+)\s*(.*)$/.exec(line);
    if (!directive) {
      if (active) out.push(line);
      continue;
    }
    const [, name, raw] = directive as unknown as [string, string, string];
    const rest = withoutComments(raw).trim();
    const top = stack[stack.length - 1];
    switch (name) {
      case 'if':
      case 'ifdef':
      case 'ifndef': {
        const cond = name === 'if' ? holds(rest, env) : env.has(rest.split(/\s+/)[0]!) === (name === 'ifdef');
        stack.push({ active: active && cond, taken: cond, outer: active });
        break;
      }
      case 'elif':
        if (top) {
          const cond = !top.taken && holds(rest, env);
          top.active = top.outer && cond;
          top.taken ||= cond;
        }
        break;
      case 'else':
        if (top) {
          top.active = top.outer && !top.taken;
          top.taken = true;
        }
        break;
      case 'endif':
        stack.pop();
        break;
      case 'define':
      case 'undef':
        if (active) {
          const [, key, value] = /^(\w+)(?:\s+(.*))?$/.exec(rest) ?? [];
          if (key && name === 'define') env.set(key, value ?? '');
          else if (key) env.delete(key);
          out.push(line);
        }
        break;
      default:
        if (active) out.push(line);
    }
  }
  return out.join('\n');
}

/**
 * The defines three.js compiles `material` with on `mesh` in a game scene: the material's own, the
 * mesh's (instancing, skinning, batching), and a sun with shadows, a hemisphere fill light and fog.
 * `defines` stands for the material's own (an onBeforeCompile may have changed `shader.defines`).
 */
export function programDefines(material: Material, mesh: Mesh | null = null, defines?: Record<string, unknown>): Record<string, string> {
  const m = material as Material & Record<string, any>;
  const own: Record<string, string> = {};
  for (const [key, value] of Object.entries((defines ?? m.defines ?? {}) as Record<string, unknown>)) if (value !== false) own[key] = String(value);
  if (m.isRawShaderMaterial) return own; // no prefix
  const d: Record<string, string> = {
    __VERSION__: '300',
    GL_ES: '',
    NUM_DIR_LIGHTS: '1',
    NUM_HEMI_LIGHTS: '1',
    NUM_DIR_LIGHT_SHADOWS: '1',
    NUM_POINT_LIGHTS: '0',
    NUM_SPOT_LIGHTS: '0',
    NUM_RECT_AREA_LIGHTS: '0',
    NUM_POINT_LIGHT_SHADOWS: '0',
    NUM_SPOT_LIGHT_SHADOWS: '0',
    NUM_SPOT_LIGHT_MAPS: '0',
    NUM_SPOT_LIGHT_COORDS: '0',
    NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS: '0',
    NUM_CLIPPING_PLANES: '0',
    UNION_CLIPPING_PLANES: '0',
    USE_SHADOWMAP: '',
    SHADOWMAP_TYPE_PCF: '',
  };
  const o = (mesh ?? {}) as Record<string, any>;
  const flags: Record<string, unknown> = {
    USE_INSTANCING: o.isInstancedMesh,
    USE_INSTANCING_COLOR: o.isInstancedMesh && o.instanceColor,
    USE_SKINNING: o.isSkinnedMesh,
    USE_BATCHING: o.isBatchedMesh,
    USE_FOG: m.fog,
    DOUBLE_SIDED: m.side === DoubleSide,
    FLAT_SHADED: m.flatShading,
    OPAQUE: !m.transparent,
    USE_COLOR: m.vertexColors,
    USE_MAP: m.map,
    USE_NORMALMAP: m.normalMap,
    USE_ROUGHNESSMAP: m.roughnessMap,
    USE_METALNESSMAP: m.metalnessMap,
    USE_EMISSIVEMAP: m.emissiveMap,
    USE_AOMAP: m.aoMap,
    USE_LIGHTMAP: m.lightMap,
    USE_ALPHAMAP: m.alphaMap,
    USE_ENVMAP: m.envMap,
    USE_UV: m.map || m.normalMap || m.roughnessMap || m.metalnessMap || m.emissiveMap || m.aoMap || m.lightMap || m.alphaMap,
  };
  for (const [key, on] of Object.entries(flags)) if (on) d[key] = '';
  return { ...d, ...own };
}

/**
 * Uniforms a replacement material's GLSL declares (includes expanded, `#ifdef`s resolved as for
 * `mesh`) that neither its `uniforms` nor three.js set: they would read 0.
 */
export function unboundUniforms(material: ShaderMaterial, mesh: Mesh | null = null): string[] {
  const defines = programDefines(material, mesh);
  const names = new Set<string>();
  for (const source of [material.vertexShader, material.fragmentShader]) for (const name of declared(activeGlsl(source, defines), 'uniform')) names.add(name);
  return [...names].filter((name) => !(name in material.uniforms) && !PROVIDED.has(name));
}

/** Of `names`, those a stage's active code uses (not as a struct member) without declaring them. */
function undeclared(active: string, names: Iterable<string>): string[] {
  const code = withoutComments(active);
  const uniforms = new Set(declared(code, 'uniform'));
  const out: string[] = [];
  for (const name of names) {
    if (uniforms.has(name) || PROVIDED.has(name) || !/^\w+$/.test(name)) continue;
    if (!new RegExp(`(?<![.\\w])${name}\\b`).test(code)) continue;
    // Declared some other way (a varying, a local, a parameter): not ours to judge.
    if (new RegExp(`\\b(?!return\\b|else\\b|case\\b)[A-Za-z_]\\w*\\s+${name}\\s*[;,=)\\[]`).test(code)) continue;
    out.push(name);
  }
  return out;
}

/**
 * Runs `material.onBeforeCompile` on three's sources for its type (a ShaderMaterial: its own) and
 * checks the result (see the top of this file). Throws on a problem.
 */
export function compileMaterial(material: Material, mesh: Mesh | null, options: { allowMiss?: Array<string | RegExp> } = {}): CompiledMaterial {
  const lib = libraryFor(material);
  const shader = {
    shaderName: material.type,
    vertexShader: lib.vertexShader,
    fragmentShader: lib.fragmentShader,
    uniforms: lib.uniforms,
    defines: { ...((material as ShaderMaterial).defines ?? {}) },
    instancing: !!(mesh as InstancedMesh | null)?.isInstancedMesh,
    skinning: !!(mesh as SkinnedMesh | null)?.isSkinnedMesh,
    isRawShaderMaterial: !!(material as { isRawShaderMaterial?: boolean }).isRawShaderMaterial,
  };
  const before = { vertex: shader.vertexShader, fragment: shader.fragmentShader };
  const replacement = !!(material as ShaderMaterial).isShaderMaterial;
  const ownUniforms = new Set(replacement ? [] : Object.keys(shader.uniforms)); // three's, before the patch adds its own
  const edits: ShaderEditCall[] = [];
  const misses: string[] = [];
  const allowed = (pattern: string | RegExp): boolean =>
    (options.allowMiss ?? []).some((a) => (typeof a === 'string' ? a === pattern : String(a) === String(pattern)));
  const proto = String.prototype as unknown as Record<'replace' | 'replaceAll', (this: string, pattern: unknown, replacement: unknown) => string>;
  const originals = { replace: proto.replace, replaceAll: proto.replaceAll };
  const wrap = (method: 'replace' | 'replaceAll') =>
    function (this: string, pattern: unknown, replacement: unknown): string {
      const subject = String(this);
      if (subject.includes('\n') && (typeof pattern === 'string' || pattern instanceof RegExp)) {
        const matched =
          typeof pattern === 'string'
            ? subject.includes(pattern)
            : new RegExp(pattern.source, pattern.flags.replace(/[gy]/g, '')).test(subject);
        edits.push({ pattern: String(pattern), matched });
        if (!matched && !allowed(pattern)) misses.push(String(pattern));
      }
      return originals[method].call(this, pattern, replacement);
    };
  proto.replace = wrap('replace');
  proto.replaceAll = wrap('replaceAll');
  try {
    material.onBeforeCompile(shader as never, null as never);
  } finally {
    proto.replace = originals.replace;
    proto.replaceAll = originals.replaceAll;
  }
  const problems: string[] = misses.map((p) => `replace() anchor did not match: ${p.length > 80 ? `${p.slice(0, 80)}…` : p}`);
  for (const [stage, source] of [['vertex', shader.vertexShader], ['fragment', shader.fragmentShader]] as const) {
    const original = before[stage];
    for (const kind of ['uniform', 'varying'] as const) {
      const names = declared(source, kind);
      const seen = new Set<string>();
      for (const name of names) {
        if (seen.has(name)) problems.push(`${stage} shader declares ${kind} ${name} twice`);
        seen.add(name);
      }
    }
    // Uniforms the patch added in GLSL must be in shader.uniforms (else they read 0).
    const own = new Set(declared(original, 'uniform'));
    for (const name of declared(source, 'uniform')) {
      if (own.has(name) || PROVIDED.has(name) || name in shader.uniforms) continue;
      problems.push(`${stage} shader declares uniform ${name}, which is not in shader.uniforms`);
    }
  }
  const defines = programDefines(material, mesh, shader.defines);
  for (const [stage, source] of [['vertex', shader.vertexShader], ['fragment', shader.fragmentShader]] as const) {
    const active = activeGlsl(source, defines);
    // A replacement's GLSL is all its own: everything it declares must be bound.
    if (replacement) {
      for (const name of declared(active, 'uniform')) {
        if (PROVIDED.has(name) || name in shader.uniforms) continue;
        problems.push(`${stage} shader declares uniform ${name}, which is not in the material's uniforms (it would read 0)`);
      }
    }
    // A uniform used where it is not declared does not compile.
    for (const name of undeclared(active, Object.keys(shader.uniforms).filter((n) => !ownUniforms.has(n)))) {
      problems.push(`${stage} shader uses uniform ${name} without declaring it`);
    }
  }
  if (problems.length) throw new Error(`Shader patch on ${material.type} "${material.name}":\n- ${[...new Set(problems)].join('\n- ')}`);
  return {
    vertexShader: shader.vertexShader,
    fragmentShader: shader.fragmentShader,
    uniforms: shader.uniforms as Record<string, { value: any }>,
    defines: shader.defines,
    edits,
  };
}

/**
 * Preview geometry by name (small; UVs everywhere, three.js's convention; `plane` lies flat, uv.y
 * running away from the camera). The gallery draws finer versions with glTF's UVs.
 */
export function previewGeometry(mesh: PreviewMesh = 'box'): BufferGeometry {
  switch (mesh) {
    case 'sphere':
      return new SphereGeometry(1, 24, 16);
    case 'plane':
      return new PlaneGeometry(4, 4, 16, 16).rotateX(-Math.PI / 2);
    case 'terrain': {
      // 200 m of gentle hills.
      const terrain = new PlaneGeometry(200, 200, 32, 32).rotateX(-Math.PI / 2);
      const position = terrain.getAttribute('position');
      for (let i = 0; i < position.count; i++) position.setY(i, 4 * Math.sin(position.getX(i) / 23) * Math.cos(position.getZ(i) / 31));
      terrain.computeVertexNormals();
      return terrain;
    }
    case 'flag':
      return new PlaneGeometry(1.6, 1, 16, 10).translate(0.8, 2.4, 0);
    case 'cylinder':
    case 'tree':
      return new CylinderGeometry(0.5, 0.5, 2, 16, 4);
    case 'grass':
      return new PlaneGeometry(0.1, 1, 1, 4).translate(0, 0.5, 0);
    case 'character':
      return new CylinderGeometry(0.35, 0.35, 1.8, 12, 6).translate(0, 0.9, 0);
    case 'building':
      return new BoxGeometry(6, 3, 4, 6, 3, 4).translate(0, 1.5, 0);
    case 'gem':
      return new IcosahedronGeometry(0.6, 0).translate(0, 0.8, 0);
    default:
      return new BoxGeometry(1, 1, 1, 2, 2, 2);
  }
}

/** A mesh for the options: plain, instanced (4) or skinned (two bones along +Y). */
function previewMesh(geometry: BufferGeometry, material: Material, options: CompileOptions): Mesh {
  if (options.instanced) {
    const mesh = new InstancedMesh(geometry, material, 4);
    for (let i = 0; i < 4; i++) mesh.setMatrixAt(i, new Matrix4().makeTranslation(i * 2, 0, 0));
    return mesh;
  }
  if (options.skinned) {
    const position = geometry.getAttribute('position');
    const indices: number[] = [];
    const weights: number[] = [];
    for (let i = 0; i < position.count; i++) {
      const w = Math.min(Math.max(position.getY(i), 0), 1);
      indices.push(0, 1, 0, 0);
      weights.push(1 - w, w, 0, 0);
    }
    geometry.setAttribute('skinIndex', new Uint16BufferAttribute(indices, 4));
    geometry.setAttribute('skinWeight', new Float32BufferAttribute(weights, 4));
    const root = new Bone();
    const tip = new Bone();
    tip.position.y = 1;
    root.add(tip);
    const mesh = new SkinnedMesh(geometry, material);
    mesh.add(root);
    mesh.bind(new Skeleton([root, tip]));
    return mesh;
  }
  return new Mesh(geometry, material);
}

/** A stand-in for loaded images: a small grey texture named after its URL. */
function fakeTexture(url: string): Promise<Texture> {
  const texture = new DataTexture(new Uint8Array(4 * 4 * 4).fill(180), 4, 4);
  texture.name = url;
  return Promise.resolve(texture);
}

/**
 * Binds game shader `shaderName` to a preview mesh exactly as a scene load does (its `load` module
 * and asset textures first), then compiles the mesh's material with the checks above. Throws when
 * the shader is unknown, its apply/mesh throws, or the patch is broken.
 */
export async function compilePatched(shaderName: string, options: CompileOptions = {}): Promise<PatchedShader> {
  const shader = getShader(shaderName);
  if (!shader) throw new Error(`No game shader "${shaderName}"`);
  const material = options.material ?? new MeshStandardMaterial({ name: PREVIEW_MATERIAL, color: 0x808080, roughness: 0.6 });
  if (!material.name) material.name = PREVIEW_MATERIAL;
  const mesh = previewMesh(previewGeometry(options.mesh), material, options);
  mesh.name = 'Preview';
  const world = new World(new Scene());
  const entity = world.spawn({ name: 'Preview', object3d: new Group().add(mesh) });
  const warnings: string[] = [];
  const time = { value: options.time ?? 0 };
  const sky = createSkyUniforms();
  const shaders = new MaterialShaders({
    renderer: () => null as never,
    time,
    sky,
    assetUrl: (path) => `/game/${path}`,
    warn: (message) => void warnings.push(message),
    loadTexture: fakeTexture,
  });
  const materials = { [material.name]: { shader: shaderName, values: options.values ?? {} } };
  await shaders.prepare(materials);
  shaders.bind([world.root], materials, () => [world.root]);
  const failed = warnings.filter((w) => w.includes(' failed on material '));
  if (failed.length) throw new Error(failed.join('\n'));
  const compile = (m: Material = mesh.material as Material): CompiledMaterial => compileMaterial(m, mesh, options);
  const compiled = compile();
  return { ...compiled, material: mesh.material as Material, original: material, mesh, entity, world, shaders, time, sky, warnings, compile };
}
