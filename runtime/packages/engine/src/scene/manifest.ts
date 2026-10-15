// Scene manifest (`<scene>.scene.json`, docs/SPEC.md §4.2): types, validation
// and default filling. DOM-free.

import { fillDefaults, getComponent } from '../schema/registry';

/** Newest manifest format this runtime reads. Version 1 files (no clips, prefabs or assets) still load. */
export const MANIFEST_FORMAT_VERSION = 2;

/** A gradient sky (the `sky` kit): linear RGB colours. */
export interface SkySettings {
  horizon: [number, number, number];
  zenith: [number, number, number];
  /** Below the horizon. */
  ground: [number, number, number];
}

/** Linear distance fog (Blender's world mist). */
export interface FogSettings {
  /** Linear RGB. */
  color: [number, number, number];
  /** Metres from the camera where fog starts and where it is full. */
  near: number;
  far: number;
}

/** How the game tone-maps, from the scene's Color Management view transform (three.js tone mapping). */
export type ToneMappingName = 'neutral' | 'agx' | 'aces' | 'none';

export const TONE_MAPPINGS: readonly ToneMappingName[] = ['neutral', 'agx', 'aces', 'none'];

export interface SceneSettings {
  /** m/s², Y-up. */
  gravity: [number, number, number];
  /** Linear RGB, or `null` for the engine default. */
  background: [number, number, number] | null;
  /** `gs_id` of the active camera, or `null`. */
  activeCamera: string | null;
  /** A gradient background (wins over `background`), or `null`. */
  sky: SkySettings | null;
  /** Distance fog, or `null`. */
  fog: FogSettings | null;
  /** Tone mapping (Blender's view transform). `'aces'` when the manifest has none (older exports). */
  toneMapping: ToneMappingName;
  /** Exposure in stops (Blender's Color Management exposure): the game multiplies by `2 ** exposure`. 0 when absent. */
  exposure: number;
}

/** A pose local to the authoring parent (Y-up), as in a live `entity.transform` item. Each part is optional. */
export interface ManifestTransform {
  p?: [number, number, number];
  /** Quaternion [x, y, z, w]. */
  r?: [number, number, number, number];
  s?: [number, number, number];
}

export interface ManifestEntity {
  id: string;
  name: string;
  /** Blender object type: MESH, EMPTY, CAMERA, LIGHT, ... */
  type: string;
  parent: string | null;
  components: Record<string, Record<string, unknown>>;
  /**
   * Scene entities only: the object's pose when it moved after its GLB was written (the GLB is then
   * left as it was). It wins over the node's transform in the GLB.
   */
  transform?: ManifestTransform;
  /** Set on collection instances of a prefab: the prefab is instantiated under this entity. */
  prefab?: string;
  /** Names of the clips that animate this entity (its object or bones), when there are any. */
  animations?: string[];
  /**
   * `false` for a collision-only object (a mesh with a Collider drawn as Wire or Bounds in Blender):
   * its meshes shape the collider but are not drawn (they move to `COLLISION_LAYER`).
   */
  drawn?: false;
}

/** An animation clip in a GLB (one per exported Blender action). */
export interface ManifestAnimation {
  name: string;
  /** Seconds. */
  duration: number;
  /** Entities whose objects (or bones) the clip animates. */
  entities: string[];
}

/** A prefab template: a Blender collection marked as a prefab. */
export interface ManifestPrefab {
  name: string;
  /** GLB file holding the template objects, relative to the manifest URL. */
  glb: string;
  /** The collection's instance offset (Y-up). */
  offset: [number, number, number];
  /** Template objects. Ids are template ids, replaced by fresh ids when instantiated. */
  entities: ManifestEntity[];
  animations: ManifestAnimation[];
}

/** A lightmap atlas: an 8-bit sRGB PNG of baked light, normalized by `scale`. */
export interface ManifestLightmapAtlas {
  /** PNG file, relative to the manifest URL (may carry a `?v=` hash). */
  file: string;
  /** Pixels (square). */
  size: number;
  /** Linear light value of a texel of 1.0. */
  scale: number;
}

/** Where an entity's lightmap is. */
export interface ManifestLightmapObject {
  /** Index into `lightmaps.atlases`. */
  atlas: number;
  /**
   * TEXCOORD index of the lightmap UVs in the entity's meshes (the three.js texture `channel`:
   * 0 `uv`, 1 `uv1`, 2 `uv2`, 3 `uv3`). The UVs are in atlas space.
   */
  uv: number;
}

/** Baked lighting (API.md §25): lightmaps of static meshes and the lamps whose light is baked. */
export interface ManifestLightmaps {
  version: number;
  /** `"indirect"`: the lightmaps hold all light except the direct light of realtime lamps. */
  mode: string;
  atlases: ManifestLightmapAtlas[];
  /** Lightmapped entities by id. Their own meshes (not child entities) get the lightmap. */
  objects: Record<string, ManifestLightmapObject>;
  /** Ids of lamps whose light is entirely in the lightmaps: the game turns them off. */
  bakedLights: string[];
}

/** A material drawn by a game shader (`defineShader`), keyed by its name in the GLBs. */
export interface ManifestMaterial {
  /** Game shader name. */
  shader: string;
  /**
   * Field values as exported (Blender writes every field). The engine coerces them to the shader's
   * fields when it applies the shader, filling missing ones with defaults.
   */
  values: Record<string, unknown>;
}

export interface SceneManifest {
  format: 'gamestudio.scene';
  formatVersion: number;
  scene: string;
  rev: number;
  exportedAt?: string;
  blenderVersion?: string;
  /** GLB file name, relative to the manifest URL. */
  glb: string;
  settings: SceneSettings;
  entities: ManifestEntity[];
  /** Clips in the scene GLB. */
  animations: ManifestAnimation[];
  prefabs: ManifestPrefab[];
  /** Project-relative asset path (as stored in `field.asset` values) → URL relative to the manifest. */
  assets: Record<string, string>;
  /** Baked lighting, or `null` when the scene has none (always set by `processManifest`). */
  lightmaps?: ManifestLightmaps | null;
  /** Game shaders by material name (always set by `processManifest`; `{}` when the scene has none). */
  materials?: Record<string, ManifestMaterial>;
  /**
   * Content hashes (hex) of the GLBs the manifest references, by file name. A GLB whose hash did not
   * change is not fetched or parsed again. Absent in older exports.
   */
  files?: Record<string, string>;
}

export interface ProcessedManifest {
  manifest: SceneManifest;
  warnings: string[];
}

export const DEFAULT_GRAVITY: [number, number, number] = [0, -9.81, 0];

function isVec(value: unknown, size: number): value is number[] {
  return Array.isArray(value) && value.length === size && value.every((n) => typeof n === 'number' && Number.isFinite(n));
}

/** `entity.transform`: the valid parts, or undefined when there are none. */
function processTransform(raw: unknown): ManifestTransform | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const t = raw as Record<string, unknown>;
  const out: ManifestTransform = {};
  if (isVec(t.p, 3)) out.p = [...(t.p as [number, number, number])];
  if (isVec(t.r, 4)) out.r = [...(t.r as [number, number, number, number])];
  if (isVec(t.s, 3)) out.s = [...(t.s as [number, number, number])];
  return out.p || out.r || out.s ? out : undefined;
}

/** `files`: GLB file name → content hash. Entries that are not non-empty strings are dropped. Absent → undefined. */
function processFiles(raw: unknown, warnings: string[]): Record<string, string> | undefined {
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    warnings.push('Ignoring "files": not an object');
    return undefined;
  }
  const out: Record<string, string> = {};
  for (const [file, hash] of Object.entries(raw as Record<string, unknown>)) {
    if (file !== '' && typeof hash === 'string' && hash !== '') out[file] = hash;
  }
  return out;
}

/**
 * Validates entity entries and fills component defaults. `unknown` collects undefined component names.
 * `transforms`: read `entity.transform` (scene entities; templates have none).
 */
function processEntities(raw: unknown, warnings: string[], unknown: Set<string>, where = '', transforms = false): ManifestEntity[] {
  const seen = new Set<string>();
  const entities: ManifestEntity[] = [];
  for (const e of Array.isArray(raw) ? raw : []) {
    if (!e || typeof e.id !== 'string' || e.id === '') {
      warnings.push(`Skipping${where} manifest entity without an id (${JSON.stringify(e?.name ?? e)})`);
      continue;
    }
    if (seen.has(e.id)) {
      warnings.push(`Duplicate entity id ${e.id}${where} (${String(e.name)}); keeping the first`);
      continue;
    }
    seen.add(e.id);
    const components: Record<string, Record<string, unknown>> = {};
    for (const [name, values] of Object.entries((e.components ?? {}) as Record<string, unknown>)) {
      const valueObject = values && typeof values === 'object' ? (values as Record<string, unknown>) : {};
      if (!getComponent(name)) unknown.add(name);
      components[name] = fillDefaults(name, valueObject) as Record<string, unknown>;
    }
    const entity: ManifestEntity = {
      id: e.id,
      name: typeof e.name === 'string' ? e.name : e.id,
      type: typeof e.type === 'string' ? e.type : 'EMPTY',
      parent: typeof e.parent === 'string' ? e.parent : null,
      components,
    };
    if (typeof e.prefab === 'string' && e.prefab !== '') entity.prefab = e.prefab;
    if (e.drawn === false) entity.drawn = false;
    if (Array.isArray(e.animations)) {
      const names = e.animations.filter((n: unknown): n is string => typeof n === 'string');
      if (names.length) entity.animations = names;
    }
    const transform = transforms ? processTransform(e.transform) : undefined;
    if (transform) entity.transform = transform;
    entities.push(entity);
  }
  return entities;
}

function processAnimations(raw: unknown): ManifestAnimation[] {
  const out: ManifestAnimation[] = [];
  for (const a of Array.isArray(raw) ? raw : []) {
    if (!a || typeof a.name !== 'string') continue;
    out.push({
      name: a.name,
      duration: typeof a.duration === 'number' && Number.isFinite(a.duration) ? a.duration : 0,
      entities: Array.isArray(a.entities) ? a.entities.filter((id: unknown): id is string => typeof id === 'string') : [],
    });
  }
  return out;
}

function processPrefabs(raw: unknown, warnings: string[], unknown: Set<string>): ManifestPrefab[] {
  const out: ManifestPrefab[] = [];
  const names = new Set<string>();
  for (const p of Array.isArray(raw) ? raw : []) {
    if (!p || typeof p.name !== 'string' || p.name === '' || typeof p.glb !== 'string' || p.glb === '') {
      warnings.push(`Skipping a prefab without a name or glb (${JSON.stringify(p?.name ?? p)})`);
      continue;
    }
    if (names.has(p.name)) {
      warnings.push(`Duplicate prefab "${p.name}"; keeping the first`);
      continue;
    }
    names.add(p.name);
    out.push({
      name: p.name,
      glb: p.glb,
      offset: isVec(p.offset, 3) ? [...(p.offset as [number, number, number])] : [0, 0, 0],
      entities: processEntities(p.entities, warnings, unknown, ` in prefab "${p.name}"`),
      animations: processAnimations(p.animations),
    });
  }
  return out;
}

const vec3 = (value: unknown): [number, number, number] => [...(value as [number, number, number])];

function processSky(raw: unknown): SkySettings | null {
  const sky = raw as Record<string, unknown> | null;
  if (!sky || typeof sky !== 'object' || !isVec(sky.horizon, 3) || !isVec(sky.zenith, 3)) return null;
  return { horizon: vec3(sky.horizon), zenith: vec3(sky.zenith), ground: isVec(sky.ground, 3) ? vec3(sky.ground) : vec3(sky.horizon) };
}

function processFog(raw: unknown, settings: Record<string, unknown>): FogSettings | null {
  const fog = raw as Record<string, unknown> | null;
  if (!fog || typeof fog !== 'object') return null;
  const near = typeof fog.near === 'number' && Number.isFinite(fog.near) ? Math.max(0, fog.near) : 0;
  const far = typeof fog.far === 'number' && Number.isFinite(fog.far) ? fog.far : NaN;
  if (!(far > near)) return null;
  const sky = settings.sky as Record<string, unknown> | undefined;
  const color = isVec(fog.color, 3) ? fog.color : isVec(sky?.horizon, 3) ? sky!.horizon : isVec(settings.background, 3) ? settings.background : [0.5, 0.5, 0.5];
  return { color: vec3(color), near, far };
}

function processAssets(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
  for (const [path, url] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof url === 'string' && url !== '') out[path] = url;
  }
  return out;
}

const isPositive = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;

/** Validates `lightmaps`; bad atlases, objects and lamp ids are dropped with a warning. Absent → `null`. */
function processLightmaps(raw: unknown, warnings: string[]): ManifestLightmaps | null {
  if (raw === undefined || raw === null) return null;
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    warnings.push('Ignoring "lightmaps": not an object');
    return null;
  }
  const l = raw as Record<string, unknown>;
  // Dropping an atlas shifts the ones after it: `index` maps manifest indexes to kept ones.
  const atlases: ManifestLightmapAtlas[] = [];
  const index = new Map<number, number>();
  (Array.isArray(l.atlases) ? l.atlases : []).forEach((a: Record<string, unknown> | null, i: number) => {
    if (!a || typeof a.file !== 'string' || a.file === '' || !isPositive(a.size) || !isPositive(a.scale)) {
      warnings.push(`Skipping lightmap atlas ${i} without a file, size or scale (${JSON.stringify(a)})`);
      return;
    }
    index.set(i, atlases.length);
    atlases.push({ file: a.file, size: a.size, scale: a.scale });
  });
  const objects: Record<string, ManifestLightmapObject> = {};
  const bad: string[] = [];
  const rawObjects = l.objects && typeof l.objects === 'object' && !Array.isArray(l.objects) ? l.objects : {};
  for (const [id, o] of Object.entries(rawObjects as Record<string, Record<string, unknown> | null>)) {
    const atlas = typeof o?.atlas === 'number' ? index.get(o.atlas) : undefined;
    const uv = o?.uv;
    if (id === '' || atlas === undefined || typeof uv !== 'number' || !Number.isInteger(uv) || uv < 0 || uv > 3) {
      bad.push(id);
      continue;
    }
    objects[id] = { atlas, uv };
  }
  if (bad.length) {
    const ids = bad.slice(0, 5).join(', ') + (bad.length > 5 ? ', ...' : '');
    warnings.push(`Skipping the lightmaps of ${bad.length} object(s) with a missing atlas or a bad uv (0..3): ${ids}`);
  }
  const bakedLights: string[] = [];
  for (const id of Array.isArray(l.bakedLights) ? l.bakedLights : []) {
    if (typeof id === 'string' && id !== '') bakedLights.push(id);
    else warnings.push(`Skipping a lightmaps.bakedLights entry that is not an id (${JSON.stringify(id)})`);
  }
  return {
    version: typeof l.version === 'number' ? l.version : 1,
    mode: typeof l.mode === 'string' ? l.mode : 'indirect',
    atlases,
    objects,
    bakedLights,
  };
}

/** Validates `materials`; entries without a shader name are dropped with a warning. Absent → `{}`. */
function processMaterials(raw: unknown, warnings: string[]): Record<string, ManifestMaterial> {
  const out: Record<string, ManifestMaterial> = {};
  if (raw === undefined || raw === null) return out;
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    warnings.push('Ignoring "materials": not an object');
    return out;
  }
  const bad: string[] = [];
  for (const [name, m] of Object.entries(raw as Record<string, Record<string, unknown> | null>)) {
    if (name === '' || !m || typeof m !== 'object' || typeof m.shader !== 'string' || m.shader === '') {
      bad.push(JSON.stringify(name));
      continue;
    }
    const values = m.values;
    const valid = values && typeof values === 'object' && !Array.isArray(values);
    if (values !== undefined && !valid) warnings.push(`Material "${name}": "values" is not an object; game shader "${m.shader}" uses its defaults`);
    out[name] = { shader: m.shader, values: valid ? structuredClone(values as Record<string, unknown>) : {} };
  }
  if (bad.length) {
    const names = bad.slice(0, 5).join(', ') + (bad.length > 5 ? ', ...' : '');
    warnings.push(`Skipping ${bad.length} "materials" entr${bad.length === 1 ? 'y' : 'ies'} without a game shader name: ${names}`);
  }
  return out;
}

/** `settings.toneMapping` and `settings.exposure`; absent → ACES and 0 (how older exports looked). */
function processToneMapping(s: Record<string, unknown>, warnings: string[]): Pick<SceneSettings, 'toneMapping' | 'exposure'> {
  let toneMapping: ToneMappingName = 'aces';
  if (s.toneMapping !== undefined) {
    if (TONE_MAPPINGS.includes(s.toneMapping as ToneMappingName)) toneMapping = s.toneMapping as ToneMappingName;
    else warnings.push(`Unknown settings.toneMapping ${JSON.stringify(s.toneMapping)}; using "aces"`);
  }
  let exposure = 0;
  if (s.exposure !== undefined) {
    if (typeof s.exposure === 'number' && Number.isFinite(s.exposure)) exposure = s.exposure;
    else warnings.push(`Ignoring settings.exposure ${JSON.stringify(s.exposure)}: not a number`);
  }
  return { toneMapping, exposure };
}

/**
 * Validates a parsed manifest and returns a fresh copy with every component's
 * missing fields filled from the registered schemas. Throws on a wrong format;
 * recoverable problems are returned as warnings.
 */
export function processManifest(raw: unknown): ProcessedManifest {
  const warnings: string[] = [];
  if (!raw || typeof raw !== 'object') throw new Error('Scene manifest is not a JSON object');
  const m = raw as Record<string, any>;
  if (m.format !== 'gamestudio.scene') throw new Error(`Not a GameStudio scene manifest (format: ${String(m.format)})`);
  if (typeof m.formatVersion !== 'number' || m.formatVersion > MANIFEST_FORMAT_VERSION) {
    warnings.push(
      `Scene formatVersion ${String(m.formatVersion)} is newer than this runtime supports (${MANIFEST_FORMAT_VERSION})`,
    );
  }
  if (typeof m.glb !== 'string' || m.glb === '') throw new Error('Scene manifest has no "glb" file');

  const s = (m.settings ?? {}) as Record<string, unknown>;
  const settings: SceneSettings = {
    gravity: isVec(s.gravity, 3) ? [...(s.gravity as [number, number, number])] : [...DEFAULT_GRAVITY],
    background: isVec(s.background, 3) ? [...(s.background as [number, number, number])] : null,
    activeCamera: typeof s.activeCamera === 'string' ? s.activeCamera : null,
    sky: processSky(s.sky),
    fog: processFog(s.fog, s),
    ...processToneMapping(s, warnings),
  };

  const unknown = new Set<string>();
  const entities = processEntities(m.entities, warnings, unknown, '', true);
  const prefabs = processPrefabs(m.prefabs, warnings, unknown);
  for (const name of unknown) {
    warnings.push(`Component "${name}" is used in the scene but not defined (src/components); its data is kept as-is`);
  }
  const files = processFiles(m.files, warnings);

  return {
    manifest: {
      format: 'gamestudio.scene',
      formatVersion: typeof m.formatVersion === 'number' ? m.formatVersion : 1,
      scene: typeof m.scene === 'string' ? m.scene : '',
      rev: typeof m.rev === 'number' ? m.rev : 0,
      exportedAt: typeof m.exportedAt === 'string' ? m.exportedAt : undefined,
      blenderVersion: typeof m.blenderVersion === 'string' ? m.blenderVersion : undefined,
      glb: m.glb,
      settings,
      entities,
      animations: processAnimations(m.animations),
      prefabs,
      assets: processAssets(m.assets),
      lightmaps: processLightmaps(m.lightmaps, warnings),
      materials: processMaterials(m.materials, warnings),
      ...(files && { files }),
    },
    warnings,
  };
}
