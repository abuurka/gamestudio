// Game shaders on Blender materials (API.md §28): binds the manifest's `materials` to the
// materials of a loaded scene and its prefab templates, applies their shaders, keeps their live
// values (`ctx.uniforms`, `ctx.shaders.set`), and applies a redefined shader again (hot module
// replacement). DOM-free except the default texture loader.
//
// A shader never edits the material as loaded: `apply` gets a clone, and the meshes are switched
// to the result only when it succeeds. The loaded material stays as it was, so a shader that
// throws leaves the Blender look, and a redefinition starts again from a fresh clone of it. The
// loaded materials are this load's own (clones of the parse cache, or a fresh parse), so a reload
// never patches a material twice.
//
// Instances: every distinct material with a manifest name (the scene's, each prefab template's and
// each lightmap variant) gets one shared instance: one apply, one set of uniforms, drawn by every
// mesh that drew the loaded material (spawned prefabs too). `ctx.shaders.set(entity, ...)` gives
// that entity its own instance: a fresh apply on a clone of the same loaded material, with uniforms
// of its own. The GLSL is the same, so three.js reuses the compiled program.

import {
  DataTexture,
  NoColorSpace,
  RepeatWrapping,
  SRGBColorSpace,
  Vector2,
  Vector3,
  Vector4,
  type Material,
  type Mesh,
  type Object3D,
  type Texture,
  type WebGLRenderer,
} from 'three';
import type { Entity } from '../ecs/entity';
import type { GameRenderer } from '../game/renderer';
import { cloneValue, type AnyFieldDef } from '../schema/fields';
import { createSkyUniforms } from '../game/sky';
import { getShader, loadedShader, loadShader, shaderValues, type ShaderContext, type ShaderImpl, type ShaderSky, type ShaderType } from '../schema/shaders';
import { loadTextureFile } from './lightmaps';
import type { ManifestMaterial } from './manifest';

/** What the shaders get from the game. */
export interface MaterialShaderHost {
  renderer(): GameRenderer;
  /** The shared time uniform (game time, seconds). */
  readonly time: { value: number };
  /** The shared sky uniforms (`ctx.sky`), kept up to date by the host. Default: the default background, no fog. */
  readonly sky?: ShaderSky;
  assetUrl(path: string): string;
  warn(message: string): void;
  /** Loads an image file as a texture. Default: three.js TextureLoader (needs a browser). */
  loadTexture?(url: string): Promise<Texture>;
  /** The meshes an entity draws itself, without those of child entities. Default: every mesh under its object. */
  ownMeshes?(entity: Entity): Mesh[];
  /** The materials are Game UI meshes' (`ctx.ui` in the shaders; GAME-UI §11.4). Default false. */
  readonly ui?: boolean;
}

/** `ctx.shaders` in systems: live values of game shaders. */
export interface ShadersAPI {
  /**
   * Changes game-shader values. `target` an entity: only its own meshes (not child entities); the
   * first call gives it its own copies of its shaded materials, so other entities keep theirs.
   * `target` a material name (as in Blender): the material everywhere it is drawn. Number, vector,
   * colour, bool and asset fields change uniforms in place (cheap, every frame is fine); a string or
   * enum field applies the shader again, with a new program (slow: not every frame).
   */
  set(target: Entity | string, values: Record<string, unknown>): void;
  /** The values in effect for an entity's shaded materials (merged when it has several) or a material; null if none. */
  get(target: Entity | string): Record<string, unknown> | null;
}

type Uniforms = Record<string, { value: unknown }>;

interface Binding {
  /** The material name (the manifest key). */
  readonly name: string;
  readonly entry: ManifestMaterial;
  /** The material's values: the manifest's, then `set` by material name. */
  readonly values: Record<string, unknown>;
  /** One shared instance per distinct material with this name. */
  readonly bound: Instance[];
}

/** One drawn copy of a bound material, with its own values and uniforms. */
interface Instance {
  readonly binding: Binding;
  /** The material as loaded (with the lightmap patch, if any). Never changed: every apply starts from a clone. */
  readonly original: Material;
  /** What its meshes draw now. */
  current: Material;
  /** The material its last apply made (disposed when replaced). */
  made: Material | null;
  /** The definition its values belong to; null until one applied. */
  shader: ShaderType | null;
  values: Record<string, unknown>;
  uniforms: Uniforms;
  /** For an entity's own instance: the entity, the values set on it, and the meshes that draw the instance. */
  readonly owner?: { readonly entity: Entity; readonly overrides: Record<string, unknown>; readonly meshes: Mesh[] };
}

/** What `bind` did. */
export interface MaterialShaderSummary {
  /** Material instances a shader was applied to. */
  applied: number;
  /** Problems found (each also passed to `warn`). */
  warnings: string[];
}

type Drawable = Object3D & { material?: Material | Material[] };
type TextureEntry = { texture: Texture | null; promise: Promise<Texture | null>; settled: boolean };

const materialsOf = (object: Drawable): Material[] =>
  Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];

/** Points `meshes` that draw `from` at `to`. Returns those that changed. */
function swapOn(meshes: Iterable<Object3D>, from: Material, to: Material): Mesh[] {
  const changed: Mesh[] = [];
  for (const object of meshes) {
    const o = object as Drawable;
    if (Array.isArray(o.material)) {
      if (!o.material.includes(from)) continue;
      o.material = o.material.map((m) => (m === from ? to : m));
    } else if (o.material === from) o.material = to;
    else continue;
    changed.push(object as Mesh);
  }
  return changed;
}

/** Points the objects under `roots` that draw `from` at `to`. Returns them. */
function swap(roots: readonly Object3D[], from: Material, to: Material): Mesh[] {
  const all: Object3D[] = [];
  for (const root of roots) root.traverse((o) => void all.push(o));
  return swapOn(all, from, to);
}

const reason = (error: unknown): string => (error instanceof Error ? error.message : String(error));

let white: Texture | null = null;
/** `ctx.white`: one 1×1 white texture. */
export function whiteTexture(): Texture {
  if (!white) {
    white = new DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
    white.name = 'gs_white';
    white.needsUpdate = true;
  }
  return white;
}

/** A field value as a uniform value: vectors and colours as three.js vectors; null for string/enum (no uniform). */
function uniformValue(def: AnyFieldDef, value: unknown): unknown {
  const v = value as number[];
  switch (def.type) {
    case 'float':
    case 'int':
    case 'bool':
      return value;
    case 'vec2':
      return new Vector2(v[0], v[1]);
    case 'vec3':
      return new Vector3(v[0], v[1], v[2]);
    case 'color':
      return new Vector4(v[0], v[1], v[2], v[3]);
    default:
      return undefined;
  }
}

/** A `set` value in field form (three.js vectors and colours become arrays), or undefined if it does not fit. */
function fieldValue(def: AnyFieldDef, value: unknown): unknown {
  const o = value as Record<string, any> | null;
  if (o && typeof o === 'object' && !Array.isArray(o)) {
    if (o.isVector2) value = [o.x, o.y];
    else if (o.isVector3) value = [o.x, o.y, o.z];
    else if (o.isVector4) value = [o.x, o.y, o.z, o.w];
    else if (o.isColor) value = [o.r, o.g, o.b, 1];
  }
  const numbers = (n: number) => Array.isArray(value) && value.length === n && value.every((x) => typeof x === 'number' && Number.isFinite(x));
  switch (def.type) {
    case 'float':
      return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
    case 'int':
      return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : undefined;
    case 'bool':
      return typeof value === 'boolean' ? value : undefined;
    case 'string':
    case 'asset':
      return typeof value === 'string' ? value : undefined;
    case 'enum':
      return typeof value === 'string' && def.options?.includes(value) ? value : undefined;
    case 'vec2':
    case 'vec3':
      return numbers(def.type === 'vec2' ? 2 : 3) ? [...(value as number[])] : undefined;
    case 'color':
      if (numbers(3)) return [...(value as number[]), 1];
      return numbers(4) ? [...(value as number[])] : undefined;
    default:
      return undefined;
  }
}

const sameValue = (a: unknown, b: unknown): boolean =>
  Array.isArray(a) && Array.isArray(b) ? a.length === b.length && a.every((x, i) => x === b[i]) : a === b;

export class MaterialShaders implements ShadersAPI {
  private bindings: Binding[] = [];
  /** Where bound materials are drawn now (the scene and the prefab templates), for redefinitions. */
  private live: () => Object3D[] = () => [];
  /** Every material an instance drew: the loaded ones and the results. Outlives a scene for kept entities. */
  private readonly drawn = new WeakMap<Material, Instance>();
  /** Entities with instances of their own. */
  private readonly entities = new Map<Entity, Instance[]>();
  /** Asset textures by colour space and URL. */
  private textures = new Map<string, TextureEntry>();
  /** Definitions waiting for their `load` module. */
  private readonly loading = new Map<ShaderType, Promise<void>>();
  /** `ctx.shaders` warnings given since the scene loaded (each once). */
  private warned = new Set<string>();
  /** `ctx.sky`: the host's, or a set of our own. */
  readonly sky: ShaderSky;

  constructor(private readonly host: MaterialShaderHost) {
    this.sky = host.sky ?? createSkyUniforms();
  }

  /**
   * Loads what `materials` needs before `bind`: the `load` modules of their shaders and the
   * textures of their asset fields, in parallel. Never rejects: a module that fails is reported by
   * `bind` (the shader "failed" on its materials), a texture that fails is a warning. `assetUrl`
   * resolves asset paths for the scene being loaded (default: the host's).
   */
  async prepare(materials: Readonly<Record<string, ManifestMaterial>>, assetUrl?: (path: string) => string): Promise<void> {
    const jobs: Array<Promise<unknown>> = [];
    for (const [name, entry] of Object.entries(materials)) {
      const shader = getShader(entry.shader);
      if (!shader) continue;
      if (shader.load) jobs.push(loadShader(shader).catch(() => {}));
      const values = shaderValues(shader, entry.values) as Record<string, unknown>;
      for (const [field, def] of Object.entries(shader.fields)) {
        if (def.type === 'asset' && values[field]) jobs.push(this.texture(values[field] as string, def, shader.name, name, assetUrl).promise);
      }
    }
    await Promise.all(jobs);
  }

  /**
   * Binds each manifest material to every material with that name under `roots` (the scene root
   * and the prefab template roots) and applies its shader: `apply` once per distinct material,
   * then `mesh` once per mesh that draws the result. Replaces the previous scene's bindings.
   * `live` returns the scene graphs a later redefinition searches. A shader whose `load` module is
   * not loaded yet (no `prepare`) is applied once it is.
   */
  bind(roots: readonly Object3D[], materials: Readonly<Record<string, ManifestMaterial>>, live: () => Object3D[]): MaterialShaderSummary {
    this.bindings = [];
    this.live = live;
    this.warned = new Set();
    const summary: MaterialShaderSummary = { applied: 0, warnings: [] };
    const report = (message: string): void => {
      if (summary.warnings.includes(message)) return; // once per load
      summary.warnings.push(message);
      this.host.warn(message);
    };
    const names = Object.keys(materials);

    const wanted = new Set(names);
    const found = new Map<string, Set<Material>>();
    for (const root of roots) {
      root.traverse((object) => {
        for (const material of materialsOf(object as Drawable)) {
          if (!wanted.has(material.name)) continue;
          let set = found.get(material.name);
          if (!set) found.set(material.name, (set = new Set()));
          set.add(material);
        }
      });
    }

    for (const name of names) {
      const entry = materials[name]!;
      const shader = getShader(entry.shader);
      if (!shader) report(`Material "${name}" uses game shader "${entry.shader}", which no src/shaders file defines`);
      const materialsNamed = found.get(name);
      if (!materialsNamed) {
        report(`Material "${name}" has game shader "${entry.shader}" but no mesh in the scene or its prefabs draws it`);
        continue;
      }
      // Kept without a shader too: defining it later (a new src/shaders file) applies it.
      const binding: Binding = { name, entry, values: { ...entry.values }, bound: [] };
      for (const material of materialsNamed) {
        const instance = this.instance(binding, material, material);
        binding.bound.push(instance);
        this.drawn.set(material, instance);
      }
      this.bindings.push(binding);
      if (!shader) continue;
      for (const instance of binding.bound) {
        if (this.apply(instance, shader, roots, report)) summary.applied++;
      }
    }
    this.pruneTextures();
    return summary;
  }

  /** Applies a (re)defined shader again to the materials bound to it, without reloading the scene. */
  redefine(shader: ShaderType): void {
    const bindings = this.bindings.filter((b) => b.entry.shader === shader.name);
    const owned = [...this.entities.values()].flat().filter((i) => i.binding.entry.shader === shader.name && i.owner!.entity.alive);
    if (!bindings.length && !owned.length) return;
    const roots = this.live();
    const reported = new Set<string>();
    const report = (message: string): void => {
      if (reported.has(message)) return; // once per redefinition
      reported.add(message);
      this.host.warn(message);
    };
    for (const binding of bindings) {
      for (const instance of binding.bound) this.apply(instance, shader, roots, report);
    }
    for (const instance of owned) this.apply(instance, shader, roots, report);
  }

  /** Forgets the bindings (the scene they were made for is gone). Entities' own instances stay with kept entities. */
  clear(): void {
    this.bindings = [];
    this.live = () => [];
  }

  /** Drops an entity's own instances (it was destroyed). */
  forget(entity: Entity): void {
    const instances = this.entities.get(entity);
    if (!instances) return;
    this.entities.delete(entity);
    for (const instance of instances) instance.made?.dispose();
  }

  // -------------------------------------------------------------------------
  // ctx.shaders

  set(target: Entity | string, values: Record<string, unknown>): void {
    if (!values || typeof values !== 'object') {
      this.warnOnce('ctx.shaders.set needs an object of field values');
      return;
    }
    if (typeof target === 'string') this.setMaterial(target, values);
    else if (target && typeof target === 'object' && 'object3d' in target) this.setEntity(target, values);
    else this.warnOnce('ctx.shaders.set needs an entity or a material name');
  }

  /** True when the loaded scene binds a game shader to the material `name`. */
  has(name: string): boolean {
    return this.bindings.some((b) => b.name === name);
  }

  /** The name of the game shader bound to the material `name` (defined or not), or null when none is. */
  shaderOf(name: string): string | null {
    return this.bindings.find((b) => b.name === name)?.entry.shader ?? null;
  }

  get(target: Entity | string): Record<string, unknown> | null {
    if (typeof target === 'string') {
      const binding = this.bindings.find((b) => b.name === target);
      if (!binding) return null;
      const shader = getShader(binding.entry.shader);
      return shader ? (shaderValues(shader, binding.values) as Record<string, unknown>) : { ...binding.values };
    }
    const instances = this.entities.get(target) ?? this.sharedOf(target);
    if (!instances.length) return null;
    const out: Record<string, unknown> = {};
    for (const instance of instances) {
      for (const [k, v] of Object.entries(instance.values)) if (!(k in out)) out[k] = cloneValue(v);
    }
    return out;
  }

  private setMaterial(name: string, values: Record<string, unknown>): void {
    const binding = this.bindings.find((b) => b.name === name);
    if (!binding) {
      this.warnOnce(`ctx.shaders.set: no material "${name}" has a game shader in this scene`);
      return;
    }
    const changes = this.accept(values, [binding.entry.shader], `material "${name}"`);
    Object.assign(binding.values, changes);
    const roots = this.live();
    for (const instance of binding.bound) this.update(instance, changes, roots);
    for (const instance of [...this.entities.values()].flat()) {
      if (instance.binding !== binding) continue;
      const inherited = Object.fromEntries(Object.entries(changes).filter(([k]) => !(k in instance.owner!.overrides)));
      this.update(instance, inherited, roots);
    }
  }

  private setEntity(entity: Entity, values: Record<string, unknown>): void {
    if (!entity.alive) return;
    const instances = this.own(entity);
    if (!instances.length) {
      this.warnOnce(`ctx.shaders.set: entity "${entity.name}" draws no material with a game shader`);
      return;
    }
    const changes = this.accept(values, [...new Set(instances.map((i) => i.binding.entry.shader))], `entity "${entity.name}"`);
    const roots = this.live();
    for (const instance of instances) {
      const fields = getShader(instance.binding.entry.shader)?.fields;
      const mine = fields ? Object.fromEntries(Object.entries(changes).filter(([k]) => k in fields)) : changes;
      Object.assign(instance.owner!.overrides, mine);
      this.update(instance, mine, roots);
    }
  }

  /** The values of `values` that fit a field of one of `shaders`, in field form; warns (once) about the rest. */
  private accept(values: Record<string, unknown>, shaders: string[], what: string): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    const defs = shaders.map((s) => getShader(s)).filter((s): s is ShaderType => !!s);
    for (const [key, raw] of Object.entries(values)) {
      const def = defs.find((s) => key in s.fields)?.fields[key];
      if (!def) {
        if (defs.length) this.warnOnce(`ctx.shaders.set(${what}): game shader ${shaders.map((s) => `"${s}"`).join(', ')} has no field "${key}"`);
        else out[key] = raw; // the shader is not defined (yet): keep the value for when it is
        continue;
      }
      const value = fieldValue(def, raw);
      if (value === undefined) this.warnOnce(`ctx.shaders.set(${what}): ${JSON.stringify(raw) ?? String(raw)} does not fit ${def.type} field "${key}"`);
      else out[key] = value;
    }
    return out;
  }

  /** Writes changed values into an instance: uniforms in place, or a new apply for string and enum fields. */
  private update(instance: Instance, changes: Record<string, unknown>, roots: readonly Object3D[]): void {
    const shader = instance.shader;
    if (!shader || getShader(shader.name) !== shader) {
      // Not applied (unknown, failed or loading): the values wait for the next apply.
      Object.assign(instance.values, changes);
      return;
    }
    let program = false;
    for (const [key, value] of Object.entries(changes)) {
      const def = shader.fields[key];
      if (!def || sameValue(instance.values[key], value)) continue;
      if (def.type === 'string' || def.type === 'enum') program = true;
      instance.values[key] = cloneValue(value);
      if (!program) this.writeUniform(instance, key, def);
    }
    if (program) this.apply(instance, shader, roots, (message) => this.warnOnce(message));
  }

  private writeUniform(instance: Instance, key: string, def: AnyFieldDef): void {
    const uniform = instance.uniforms[key];
    if (!uniform) return;
    const value = instance.values[key];
    if (def.type === 'asset') return this.attachTexture(instance, key, def);
    const target = uniform.value as { set?: (...n: number[]) => unknown } | null;
    if (Array.isArray(value) && target?.set) target.set(...value);
    else uniform.value = value;
  }

  /** The entity's own instances, made on the first call from the shared instances its meshes draw. */
  private own(entity: Entity): Instance[] {
    const existing = this.entities.get(entity);
    if (existing) return existing;
    const meshes = this.ownMeshes(entity);
    const bySource = new Map<Instance, Mesh[]>();
    for (const mesh of meshes) {
      for (const material of materialsOf(mesh as Drawable)) {
        const shared = this.drawn.get(material);
        if (!shared || shared.owner) continue;
        let list = bySource.get(shared);
        if (!list) bySource.set(shared, (list = []));
        if (!list.includes(mesh)) list.push(mesh);
      }
    }
    if (!bySource.size) return [];
    const roots = this.live();
    const instances: Instance[] = [];
    for (const [shared, drawing] of bySource) {
      const instance = this.instance(shared.binding, shared.original, shared.current, { entity, overrides: {}, meshes: drawing });
      instances.push(instance);
      const shader = getShader(shared.binding.entry.shader);
      if (shader) this.apply(instance, shader, roots, (message) => this.warnOnce(message));
    }
    this.entities.set(entity, instances);
    return instances;
  }

  /** The shared instances an entity's meshes draw (no instances are made). */
  private sharedOf(entity: Entity): Instance[] {
    const out = new Set<Instance>();
    for (const mesh of this.ownMeshes(entity)) {
      for (const material of materialsOf(mesh as Drawable)) {
        const instance = this.drawn.get(material);
        if (instance) out.add(instance);
      }
    }
    return [...out];
  }

  private ownMeshes(entity: Entity): Mesh[] {
    if (this.host.ownMeshes) return this.host.ownMeshes(entity);
    const meshes: Mesh[] = [];
    entity.object3d.traverse((o) => {
      if ((o as Mesh).isMesh) meshes.push(o as Mesh);
    });
    return meshes;
  }

  private warnOnce(message: string): void {
    if (this.warned.has(message)) return;
    this.warned.add(message);
    this.host.warn(message);
  }

  // -------------------------------------------------------------------------
  // Applying

  private instance(binding: Binding, original: Material, current: Material, owner?: Instance['owner']): Instance {
    const shader = getShader(binding.entry.shader);
    const raw = { ...binding.values, ...owner?.overrides };
    return {
      binding,
      original,
      current,
      made: null,
      shader: null,
      values: shader ? (shaderValues(shader, raw) as Record<string, unknown>) : raw,
      uniforms: {},
      owner,
    };
  }

  /**
   * Applies `shader` to a fresh clone of the loaded material and puts the result on the meshes that
   * draw the instance now (under `roots`, or an entity's own meshes). On an exception they get the
   * loaded material. A shader whose module is still loading is applied when it has loaded.
   */
  private apply(instance: Instance, shader: ShaderType, roots: readonly Object3D[], report: (message: string) => void): boolean {
    const { binding, owner } = instance;
    const loaded = loadedShader(shader);
    if (!loaded) {
      this.whenLoaded(shader);
      return false;
    }
    const previous = instance.current;
    const values = shaderValues(shader, { ...binding.values, ...owner?.overrides }) as Record<string, unknown>;
    instance.shader = shader;
    instance.values = values;
    instance.uniforms = this.uniforms(instance, shader);
    const ctx = this.context(binding.name, shader.name, instance.uniforms);
    const madeBefore = instance.made;
    const put = (from: Material, to: Material): Mesh[] => (owner ? swapOn(owner.meshes, from, to) : swap(roots, from, to));
    try {
      if ('error' in loaded) throw loaded.error;
      const impl: ShaderImpl = loaded.impl;
      const work = instance.original.clone(); // clones keep patches made before (the lightmap's)
      const returned = impl.apply?.(work, values as never, ctx as never);
      if (returned != null && !(returned as Material).isMaterial) throw new Error('apply returned something that is not a three.js material');
      const result = (returned as Material | undefined) ?? work;
      const meshes = put(previous, result);
      instance.current = instance.made = result;
      this.drawn.set(result, instance);
      if (impl.mesh) for (const mesh of meshes) impl.mesh(mesh, values as never, ctx as never);
      return true;
    } catch (error) {
      if (instance.current !== instance.original) put(instance.current, instance.original);
      instance.current = instance.original;
      instance.made = null;
      instance.shader = null;
      report(`Game shader "${shader.name}" failed on material "${binding.name}": ${reason(error)}`);
      return false;
    } finally {
      // The previous result of this instance is no longer drawn.
      if (madeBefore && madeBefore !== instance.current) madeBefore.dispose();
    }
  }

  /** Loads a definition's module, then applies it where it is bound (if it is still the definition). */
  private whenLoaded(shader: ShaderType): void {
    if (this.loading.has(shader)) return;
    const done = (): void => {
      this.loading.delete(shader);
      if (getShader(shader.name) === shader) this.redefine(shader);
    };
    this.loading.set(shader, loadShader(shader).then(done, done));
  }

  /**
   * Resolves once nothing is pending: every bound shader's `load` module has loaded and been applied
   * where it is bound, and every asset texture asked for has loaded (or failed, with its warning).
   * Work that starts meanwhile (an apply asking for another texture) is waited for too. Never rejects.
   */
  async ready(): Promise<void> {
    for (let round = 0; round < 20; round++) {
      const pending: Array<Promise<unknown>> = [...this.loading.values()];
      for (const entry of this.textures.values()) if (!entry.settled) pending.push(entry.promise);
      if (pending.length === 0) return;
      await Promise.all(pending);
      await Promise.resolve(); // what waits on them (a texture landing in its uniform) runs first
    }
  }

  /** Fresh uniforms for an instance's values (asset fields get their textures as they load). */
  private uniforms(instance: Instance, shader: ShaderType): Uniforms {
    const uniforms: Uniforms = {};
    for (const [key, def] of Object.entries(shader.fields)) {
      if (def.type === 'asset') uniforms[key] = { value: null };
      else {
        const value = uniformValue(def, instance.values[key]);
        if (value !== undefined) uniforms[key] = { value };
      }
    }
    // Assigned before textures attach: a texture already loaded is set at once.
    instance.uniforms = uniforms;
    for (const [key, def] of Object.entries(shader.fields)) if (def.type === 'asset') this.attachTexture(instance, key, def);
    return uniforms;
  }

  /** Points an asset uniform at its texture: now if it is loaded, else when it loads (if the value is still the same). */
  private attachTexture(instance: Instance, key: string, def: AnyFieldDef): void {
    const uniform = instance.uniforms[key];
    const path = instance.values[key] as string;
    if (!uniform) return;
    if (!path) {
      uniform.value = null;
      return;
    }
    const entry = this.texture(path, def, instance.binding.entry.shader, instance.binding.name);
    if (entry.texture) uniform.value = entry.texture;
    else {
      void entry.promise.then((texture) => {
        if (texture && instance.uniforms[key] === uniform && instance.values[key] === path) uniform.value = texture;
      });
    }
  }

  /** The texture of an asset path, loaded once per colour space (repeat wrapping, glTF orientation, mipmaps). */
  private texture(path: string, def: AnyFieldDef, shader: string, material: string, assetUrl?: (path: string) => string): TextureEntry {
    const srgb = def.colorSpace === 'srgb';
    const url = (assetUrl ?? this.host.assetUrl.bind(this.host))(path);
    const key = `${srgb ? 'srgb' : 'linear'}|${url}`;
    let entry = this.textures.get(key);
    if (entry) return entry;
    const load = this.host.loadTexture ?? loadTextureFile;
    const e: TextureEntry = { texture: null, promise: null as never, settled: false };
    let started: Promise<Texture>;
    try {
      started = Promise.resolve(load(url)); // starts now
    } catch (error) {
      started = Promise.reject(error);
    }
    e.promise = started
      .then((texture) => {
        texture.name = path;
        texture.colorSpace = srgb ? SRGBColorSpace : NoColorSpace;
        texture.wrapS = texture.wrapT = RepeatWrapping;
        texture.flipY = false; // glTF UV convention, like the GLB's own textures
        texture.anisotropy = this.anisotropy();
        texture.needsUpdate = true;
        e.settled = true;
        return (e.texture = texture);
      })
      .catch((error: unknown) => {
        e.settled = true;
        const why = error instanceof Error ? error.message : 'missing or not an image';
        this.host.warn(`Game shader "${shader}" (material "${material}"): texture ${path} did not load (${why})`);
        return null;
      });
    this.textures.set(key, (entry = e));
    return entry;
  }

  private anisotropy(): number {
    try {
      const raw = this.host.renderer()?.raw as WebGLRenderer | undefined;
      return Math.min(8, raw?.capabilities?.getMaxAnisotropy?.() ?? 4);
    } catch {
      return 4;
    }
  }

  /** Disposes textures no instance uses any more (after a scene change). */
  private pruneTextures(): void {
    const used = new Set<Texture>();
    const instances = [...this.bindings.flatMap((b) => b.bound), ...[...this.entities.values()].flat()];
    for (const instance of instances) {
      for (const uniform of Object.values(instance.uniforms)) {
        if ((uniform.value as Texture | null)?.isTexture) used.add(uniform.value as Texture);
      }
    }
    for (const [key, entry] of this.textures) {
      if (!entry.texture || used.has(entry.texture)) continue;
      this.textures.delete(key);
      entry.texture.dispose();
    }
  }

  private context(material: string, shader: string, uniforms: Uniforms): ShaderContext {
    const host = this.host;
    return {
      material,
      get renderer() {
        return host.renderer();
      },
      time: host.time,
      sky: this.sky,
      ui: host.ui === true,
      uniforms: uniforms as ShaderContext['uniforms'],
      white: whiteTexture(),
      assetUrl: (path) => host.assetUrl(path),
      warn: (message) => host.warn(`Game shader "${shader}" (material "${material}"): ${message}`),
    };
  }
}
