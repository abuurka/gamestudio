// Game shaders (docs/SPEC.md §6.5, API.md §28): code that draws a Blender material in the game. A
// material names its shader in Blender (the manifest's `materials`); the engine binds it on every
// scene load (scene/materials.ts). Like the component registry this module is DOM-free and imports
// Three.js for types only: the dev server loads it in Node to write `.gamestudio/components.json`.
// The built-in shader library (src/shaders/library) registers itself through `../schema.ts`.

import type { Color, Material, Mesh, Texture, Vector2, Vector3, Vector4, WebGLProgramParametersWithUniforms, WebGLRenderer } from 'three';
import type { GameRenderer } from '../game/renderer';
import { checkDefinition, fillFields, type AnyFieldDef, type FieldDef } from './fields';
import type { ValuesOf } from './registry';

/** The categories of the built-in shader library (exact strings: Blender groups shaders by them). */
export const SHADER_CATEGORIES = ['Water', 'Nature', 'Magic & Sci-fi', 'Surfaces', 'Transformations', 'Gameplay', 'Style'] as const;

/**
 * The uniform value of a field: float/int → number, bool → boolean, vec2/vec3 → Vector2/Vector3,
 * color → Vector4 (linear RGBA), asset → the loaded texture or null. String and enum fields have none.
 */
export type UniformValueOf<D> =
  D extends FieldDef<infer T, any>
    ? T extends 'float' | 'int'
      ? number
      : T extends 'bool'
        ? boolean
        : T extends 'vec2'
          ? Vector2
          : T extends 'vec3'
            ? Vector3
            : T extends 'color'
              ? Vector4
              : T extends 'asset'
                ? Texture | null
                : never
    : never;

/** `ctx.uniforms` of a shader with fields `F`: one `{ value }` per field, except string and enum fields. */
export type ShaderUniforms<F> = {
  readonly [K in keyof F as [UniformValueOf<F[K]>] extends [never] ? never : K]: { value: UniformValueOf<F[K]> };
};

/**
 * The scene's sky as uniforms (`ctx.sky`): one set shared by every shader, which the engine updates
 * on every scene load from the scene's settings, as it draws the background (Blender's world: the
 * sky kit's gradient, else its flat colour in every direction) and the fog (Blender's mist).
 * Colours are linear RGB (`vec3` in GLSL). `skyGlsl` samples them like the background.
 */
export interface ShaderSky {
  /** Straight up. */
  readonly zenith: { value: Color };
  /** At the horizon. */
  readonly horizon: { value: Color };
  /** Below the horizon. */
  readonly ground: { value: Color };
  /** The distance fog's colour; the horizon's when the scene has no fog. */
  readonly fog: { value: Color };
  /** Metres from the camera where the fog starts; 0 when the scene has no fog. */
  readonly fogNear: { value: number };
  /** Metres from the camera where the fog is full; 0 when the scene has no fog. */
  readonly fogFar: { value: number };
}

/** The third argument of a game shader's `apply` and `mesh`. */
export interface ShaderContext<F = Record<string, AnyFieldDef>> {
  /** The material's name in the manifest (as in Blender and the GLB). */
  readonly material: string;
  readonly renderer: GameRenderer;
  /**
   * One uniform object shared by every shader: game time in seconds (`ctx.time`), updated every frame
   * by the engine, so playtests stay deterministic. Put it in `shader.uniforms` as it is.
   */
  readonly time: { value: number };
  /**
   * The scene's sky and fog colours: uniform objects shared by every shader, updated on every scene
   * load. Put them in `shader.uniforms` as they are (`skyUniforms(ctx.sky, prefix)` with `skyGlsl`).
   */
  readonly sky: ShaderSky;
  /**
   * True when the material is a Game UI mesh's (GAME-UI §11.4): a flat, unlit `MeshBasicMaterial` in
   * local px with y down (Blender's y up is −y), drawn without depth or tone mapping. `time` is then
   * the UI clock: seconds since the canvas opened, running while the game is paused. False for level
   * materials.
   */
  readonly ui: boolean;
  /**
   * This material instance's values as uniforms, one per field (string and enum fields have none:
   * they belong in the program). Put them in `shader.uniforms` as they are
   * (`shader.uniforms.uSpeed = ctx.uniforms.speed`): `ctx.shaders.set` changes their values in place.
   * An asset field holds its texture, `null` while it loads or when the field is empty.
   */
  readonly uniforms: ShaderUniforms<F>;
  /** A 1×1 white texture, for a sampler whose asset field is empty or still loading. */
  readonly white: Texture;
  /** URL of a project-relative asset path (an `asset` field's value). */
  assetUrl(path: string): string;
  /** Logs a warning that reaches Blender's console and the MCP logs. */
  warn(message: string): void;
}

/** A game shader's `apply`: once per bound material instance. */
export type ShaderApply<F = Record<string, AnyFieldDef>> = (material: Material, values: ValuesOf<F>, ctx: ShaderContext<F>) => Material | void;

/** A game shader's `mesh`: once per mesh that draws the (resulting) material. */
export type ShaderMesh<F = Record<string, AnyFieldDef>> = (mesh: Mesh, values: ValuesOf<F>, ctx: ShaderContext<F>) => void;

/** What a lazily loaded shader module exports (`load: () => import('./impl')`). */
export interface ShaderImpl<F = Record<string, AnyFieldDef>> {
  apply?: ShaderApply<F>;
  mesh?: ShaderMesh<F>;
}

/** How the shader gallery (and thumbnails) show a shader. Not in the registry. */
export interface ShaderPreview<V = Record<string, unknown>> {
  /**
   * The preview mesh: `terrain` is 200 m of gentle hills, `flag` a cloth on a pole, `building` a
   * hollow house with metre UVs, `gem` a faceted stone. Every mesh has UVs as a GLB from Blender has
   * them (V flipped).
   */
  mesh?: 'sphere' | 'box' | 'plane' | 'cylinder' | 'character' | 'tree' | 'grass' | 'terrain' | 'flag' | 'building' | 'gem';
  /** Field values for the preview, over the defaults. */
  values?: Partial<V>;
  /** Game time of the picture, seconds. */
  t?: number;
  /** `eye`: standing on the ground at eye height, looking across (big surfaces at game scale). */
  camera?: 'near' | 'far' | 'top' | 'eye';
  /** The preview material's base colour (linear RGBA), when the mesh's default grey does not show the shader. */
  color?: [number, number, number, number];
  /** The preview material blends, as Blender's Alpha Blend exports (transparent, no depth writes). */
  transparent?: boolean;
  /** Scales the preview mesh (and the framing): `plane` with 25 is a 200 m sea. */
  scale?: number;
  /** A wall between the camera and the left half of the subject: for shaders that show what is hidden. */
  occluder?: boolean;
}

export interface ShaderOptions<F extends Record<string, AnyFieldDef>> {
  /** Shown in Blender. */
  description?: string;
  /** Groups shaders in Blender, e.g. "Water" or "Surfaces". */
  category?: string;
  fields?: F;
  /**
   * Once per bound three.js material. Patch it in place (`patchShader`) and return nothing, or
   * return a replacement material (the engine puts it on every mesh that drew the original).
   */
  apply?: ShaderApply<F>;
  /** Once per mesh that draws the (resulting) material. */
  mesh?: ShaderMesh<F>;
  /**
   * Instead of `apply` and `mesh`: loads the module that exports them (`() => import('./impl')`), so
   * a build puts them in a chunk of their own that only games using the shader download. A scene
   * load waits for the shaders its manifest names.
   */
  load?: () => Promise<ShaderImpl<F>>;
  /** For the shader gallery and thumbnails. */
  preview?: ShaderPreview<ValuesOf<F>>;
  /**
   * The shader works on Game UI meshes too (GAME-UI §11.4): flat, unlit `MeshBasicMaterial`s in local
   * px with y down, without normals, lights or depth (`ctx.ui` is true there). Canvases apply only
   * shaders that say so; others warn once and the element draws without them.
   */
  ui?: boolean;
  /** @internal Set only by the engine for built-in shaders (the library). */
  builtin?: boolean;
}

/** A game shader handle returned by `defineShader`. Bindings look shaders up by name. */
export interface ShaderType<V = Record<string, unknown>> {
  readonly name: string;
  readonly description?: string;
  readonly category?: string;
  readonly builtin: boolean;
  /** Works on Game UI meshes (`ui: true` in its definition). */
  readonly ui: boolean;
  readonly fields: Readonly<Record<string, AnyFieldDef>>;
  readonly preview?: ShaderPreview<V>;
  apply?(material: Material, values: V, ctx: ShaderContext<any>): Material | void;
  mesh?(mesh: Mesh, values: V, ctx: ShaderContext<any>): void;
  load?(): Promise<ShaderImpl<any>>;
}

/** Value type of a shader handle: `ShaderValues<typeof Triplanar>`. */
export type ShaderValues<S> = S extends ShaderType<infer V> ? V : never;

const shaders = new Map<string, ShaderType>();
const listeners = new Set<(shader: ShaderType) => void>();

/**
 * Defines (or redefines) a game shader. Names follow the component rule (PascalCase) in a namespace
 * of their own; fields use the `field.*` builders, except `field.entity`. Redefinition replaces the
 * shader, and a running game applies the new one to its materials (hot module replacement). A
 * project's `src/shaders` file may define a library shader's name: the later definition wins.
 */
export function defineShader<F extends Record<string, AnyFieldDef> = Record<string, never>>(
  name: string,
  options: ShaderOptions<F> = {},
): ShaderType<ValuesOf<F>> {
  const fields = options.fields ?? ({} as F);
  checkDefinition('shader', name, fields);
  for (const hook of ['apply', 'mesh', 'load'] as const) {
    if (options[hook] !== undefined && typeof options[hook] !== 'function') {
      throw new Error(`Game shader "${name}": ${hook} must be a function`);
    }
  }
  if (options.load && (options.apply || options.mesh)) {
    throw new Error(`Game shader "${name}": give either load or apply/mesh, not both`);
  }
  if (options.category !== undefined && (typeof options.category !== 'string' || options.category === '')) {
    throw new Error(`Game shader "${name}": category must be a non-empty string`);
  }
  if (options.ui !== undefined && typeof options.ui !== 'boolean') {
    throw new Error(`Game shader "${name}": ui must be true or false`);
  }
  type Handle = ShaderType<ValuesOf<F>>;
  const shader: Handle = Object.freeze({
    name,
    description: options.description,
    category: options.category,
    builtin: options.builtin === true,
    ui: options.ui === true,
    fields: Object.freeze({ ...fields }),
    preview: options.preview,
    apply: options.apply as Handle['apply'],
    mesh: options.mesh as Handle['mesh'],
    load: options.load as Handle['load'],
  });
  shaders.set(name, shader as ShaderType);
  for (const listener of listeners) listener(shader as ShaderType);
  return shader;
}

/** The current definition for a name (or for a possibly stale handle). */
export function getShader(shader: string | ShaderType<any>): ShaderType | undefined {
  return shaders.get(typeof shader === 'string' ? shader : shader.name);
}

/** All registered game shaders, sorted by name. */
export function listShaders(): ShaderType[] {
  return [...shaders.values()].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** Calls `listener` whenever a game shader is defined or redefined. Returns an unsubscribe function. */
export function onShaderDefined(listener: (shader: ShaderType) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** A complete value object for a shader: known fields from `values` (coerced), the rest from defaults. */
export function shaderValues<V>(shader: ShaderType<V>, values: Record<string, unknown> = {}): V {
  return fillFields(shader.fields, values) as V;
}

// ---------------------------------------------------------------------------
// Lazily loaded shaders (`load`)

type LoadState = { promise: Promise<ShaderImpl>; impl?: ShaderImpl; failed?: { error: unknown } };
/** By definition object: a redefinition loads its own module. */
const loads = new WeakMap<ShaderType, LoadState>();

function implOf(shader: ShaderType, module: unknown): ShaderImpl {
  const m = (module ?? {}) as Record<string, unknown>;
  const impl: ShaderImpl = {};
  for (const hook of ['apply', 'mesh'] as const) {
    if (m[hook] === undefined) continue;
    if (typeof m[hook] !== 'function') throw new Error(`the loaded module's ${hook} is not a function`);
    impl[hook] = m[hook] as never;
  }
  if (!impl.apply && !impl.mesh) throw new Error('the loaded module exports neither apply nor mesh');
  return impl;
}

/**
 * The shader's `apply` and `mesh`: the inline ones, or those of its `load` module (loaded once per
 * definition and shared). Rejects when the module fails to load or exports neither.
 */
export function loadShader(shader: ShaderType<any>): Promise<ShaderImpl> {
  if (!shader.load) return Promise.resolve({ apply: shader.apply, mesh: shader.mesh });
  let state = loads.get(shader);
  if (!state) {
    const s: LoadState = { promise: null as never };
    s.promise = Promise.resolve()
      .then(() => shader.load!())
      .then((module) => (s.impl = implOf(shader, module)))
      .catch((error: unknown) => {
        s.failed = { error };
        throw error;
      });
    s.promise.catch(() => {}); // callers that care await it themselves
    loads.set(shader, (state = s));
  }
  return state.promise;
}

/**
 * @internal What `loadShader` has for a definition now: its hooks, its load error, or `undefined`
 * while its `load` module is not loaded (or was never asked for).
 */
export function loadedShader(shader: ShaderType<any>): { impl: ShaderImpl } | { error: unknown } | undefined {
  if (!shader.load) return { impl: { apply: shader.apply, mesh: shader.mesh } };
  const state = loads.get(shader);
  if (state?.impl) return { impl: state.impl };
  if (state?.failed) return { error: state.failed.error };
  return undefined;
}

// ---------------------------------------------------------------------------
// Shader patches

/** Edits the shader source three.js is about to compile (WebGL; `Material.onBeforeCompile`). */
export type ShaderEdit = (shader: WebGLProgramParametersWithUniforms, renderer: WebGLRenderer) => void;

/** Patch keys by material. A WeakMap, not `userData`: `Material.clone` copies userData but not the hooks. */
const patchKeys = new WeakMap<Material, Set<string>>();

/**
 * Adds `edit` to the material's `onBeforeCompile`, after whatever was there (it is called first), and
 * `|key` to its `customProgramCacheKey`, so patches compose with each other and with the engine's
 * lightmap patch. The key names the program: patches that write different GLSL need different keys,
 * and per-material values belong in uniforms. Patching a material twice with one key does nothing.
 * Copies made with `material.clone()` keep the patches. WebGL only: the WebGPU renderer ignores
 * `onBeforeCompile`.
 */
export function patchShader(material: Material, key: string, edit: ShaderEdit): void {
  if (typeof key !== 'string' || key === '') throw new Error('patchShader needs a key: a non-empty string naming the patch');
  if (typeof edit !== 'function') throw new Error(`patchShader "${key}": edit must be a function`);
  let keys = patchKeys.get(material);
  if (keys?.has(key)) return;
  if (!keys) patchKeys.set(material, (keys = new Set()));
  keys.add(key);
  const previous = material.onBeforeCompile;
  const previousKey = material.customProgramCacheKey;
  material.onBeforeCompile = function (this: Material, shader, renderer) {
    previous.call(this, shader, renderer);
    edit.call(this, shader, renderer);
  };
  material.customProgramCacheKey = function (this: Material) {
    return `${previousKey.call(this)}|${key}`;
  };
  (material as { clone: () => Material }).clone = clonePatched;
  material.needsUpdate = true; // a material drawn before gets the patched program
}

// ---------------------------------------------------------------------------
// The sky in GLSL

/**
 * GLSL that samples the scene's sky the way the game draws its background: declares
 * `uniform vec3 <p>SkyZenith, <p>SkyHorizon, <p>SkyGround` and `vec3 <p>Sky(vec3 direction)` (a world
 * direction, +Y up: the ground colour below the horizon, then horizon to zenith, as Blender's sky
 * kit ramps them). Put it in the fragment shader once, and `skyUniforms(ctx.sky, p)` in
 * `shader.uniforms`. The prefix keeps two patches on one material apart.
 */
export function skyGlsl(prefix: string): string {
  if (!/^[A-Za-z_]\w*$/.test(prefix)) throw new Error(`skyGlsl: "${prefix}" is not a GLSL name prefix`);
  const p = prefix;
  return /* glsl */ `
uniform vec3 ${p}SkyZenith;
uniform vec3 ${p}SkyHorizon;
uniform vec3 ${p}SkyGround;
vec3 ${p}Sky(vec3 direction) {
  float t = clamp(normalize(direction).y * 0.5 + 0.5, 0.0, 1.0);
  vec3 c = mix(${p}SkyGround, ${p}SkyHorizon, clamp((t - 0.47) / 0.03, 0.0, 1.0));
  c = mix(c, mix(${p}SkyHorizon, ${p}SkyZenith, 0.45), clamp((t - 0.5) / 0.1, 0.0, 1.0));
  return mix(c, ${p}SkyZenith, clamp((t - 0.6) / 0.4, 0.0, 1.0));
}`;
}

/** The `shader.uniforms` of `skyGlsl(prefix)`: the shared sky uniforms (`ctx.sky`) under its names. */
export function skyUniforms(sky: ShaderSky, prefix: string): Record<string, { value: Color }> {
  return { [`${prefix}SkyZenith`]: sky.zenith, [`${prefix}SkyHorizon`]: sky.horizon, [`${prefix}SkyGround`]: sky.ground };
}

/** `material.clone()` for patched materials: three.js copies neither `onBeforeCompile` nor the cache key. */
function clonePatched(this: Material): Material {
  const copy = (Object.getPrototypeOf(this) as Material).clone.call(this) as Material;
  copy.onBeforeCompile = this.onBeforeCompile;
  copy.customProgramCacheKey = this.customProgramCacheKey;
  (copy as { clone: () => Material }).clone = clonePatched;
  patchKeys.set(copy, new Set(patchKeys.get(this)));
  return copy;
}
