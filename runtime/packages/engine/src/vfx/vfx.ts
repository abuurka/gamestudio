// Particle effects (`ctx.vfx`) and the built-in ParticleEmitter component. Each emitter is one draw
// call: camera-facing quads instanced from a pooled interleaved buffer, written only when the game
// draws. The simulation runs in the game's fixed steps (so it pauses with the game and repeats
// exactly under the test clock) with a random stream per emitter.

import {
  AdditiveBlending,
  BufferAttribute,
  Color as ThreeColor,
  DoubleSide,
  DynamicDrawUsage,
  InstancedBufferGeometry,
  InstancedInterleavedBuffer,
  InterleavedBufferAttribute,
  Matrix4,
  Mesh,
  NormalBlending,
  Quaternion,
  ShaderMaterial,
  SRGBColorSpace,
  TextureLoader,
  Vector3,
  type Camera,
  type Texture,
} from 'three';
import { componentName, type Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { hashSeed, mulberry32 } from '../game/random';
import { isVec3Input, toVector3, type Vec3Input } from '../math';
import { ParticleEmitter } from '../schema/builtins';
import type { Color } from '../schema/fields';
import { fillDefaults } from '../schema/registry';
import { EmitterCore, lifeAlpha, ParticlePool, type EmitFrame, type ParticleSettings } from './particles';
import { PRESET_LAYERS, VFX_PRESETS, type VfxPreset } from './presets';
import { particleTexture, PARTICLE_TEXTURES, type ParticleTexture } from './textures';

export type { ParticleSettings } from './particles';
export { VFX_PRESETS, type VfxPreset } from './presets';

/** Options for `ctx.vfx.burst`: any ParticleEmitter field, plus a few shortcuts. */
export interface ParticleOptions extends Partial<Omit<ParticleSettings, 'colorStart' | 'colorEnd' | 'palette'>> {
  /** A named effect to start from (when the options are the second argument). */
  preset?: VfxPreset;
  /** Multiplies sizes, speeds, the radius and the box. Default 1. */
  scale?: number;
  /** Multiplies the number of particles (burst and rate). Default 1. */
  amount?: number;
  /** Replaces the colour (keeps the opacity over life): a CSS colour or linear `[r, g, b, a]`. */
  color?: string | Color;
  colorStart?: string | Color;
  colorEnd?: string | Color;
  /** Each particle takes a random colour from this list. */
  palette?: Array<string | Color>;
  /** With an entity: keep emitting from where it is (for effects with a `rate`). Default false: emit where it is now. */
  follow?: boolean;
}

/** A running burst. */
export interface VfxEffect {
  /** False once it stopped and its last particle died. */
  readonly alive: boolean;
  /** Live particles. */
  readonly count: number;
  /** Stops emitting; live particles finish their life. */
  stop(): void;
}

export interface VfxAPI {
  /**
   * Plays an effect once at an entity (where it is now) or a world point: a preset name
   * (`'sparkle'`, `'pickup'`, `'dust'`, `'smoke'`, `'explosion'`, `'confetti'`) or options.
   * `ctx.vfx.burst(coin, 'pickup'); coin.destroy();` still plays the whole effect.
   */
  burst(at: Entity | Vec3Input, effect?: VfxPreset | ParticleOptions, options?: ParticleOptions): VfxEffect;
  /** Starts (or restarts) the entity's ParticleEmitter. */
  play(entity: Entity): void;
  /** Stops the entity's emitter; `clear: true` also removes its live particles. */
  stop(entity: Entity, options?: { clear?: boolean }): void;
  /** Emits `count` particles from the entity's ParticleEmitter now, playing or not. */
  emit(entity: Entity, count: number): void;
  /** The entity's ParticleEmitter is emitting. */
  isPlaying(entity: Entity): boolean;
  /** Live particles: of an entity's emitter, of an effect, or (no argument) of everything. */
  count(target?: Entity | VfxEffect): number;
  /** The preset names. */
  readonly presets: readonly VfxPreset[];
  /** Removes every particle and every burst. */
  clear(): void;
}

const FLOATS = 12; // offset 3, color 4, size + angle 2, velocity 3

const VERTEX = /* glsl */ `
attribute vec3 iOffset;
attribute vec4 iColor;
attribute vec2 iSize;
attribute vec3 iVelocity;
uniform float stretch;
varying vec2 vUv;
varying vec4 vColor;
void main() {
  vec4 mv = modelViewMatrix * vec4(iOffset, 1.0);
  vec2 corner = position.xy;
  vec2 offset;
  if (stretch > 0.0) {
    vec2 v = (modelViewMatrix * vec4(iVelocity, 0.0)).xy;
    float speed = length(v);
    vec2 along = speed > 1e-5 ? v / speed : vec2(0.0, 1.0);
    vec2 across = vec2(along.y, -along.x);
    offset = across * corner.x * iSize.x + along * corner.y * (iSize.x + stretch * speed);
  } else {
    float c = cos(iSize.y);
    float s = sin(iSize.y);
    offset = vec2(c * corner.x - s * corner.y, s * corner.x + c * corner.y) * iSize.x;
  }
  mv.xy += offset;
  gl_Position = projectionMatrix * mv;
  vUv = uv;
  vColor = iColor;
}
`;

const FRAGMENT = /* glsl */ `
uniform sampler2D map;
varying vec2 vUv;
varying vec4 vColor;
void main() {
  vec4 tex = texture2D(map, vUv);
  gl_FragColor = vec4(vColor.rgb * tex.rgb, vColor.a * tex.a);
  if (gl_FragColor.a < 0.003) discard;
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;

/** A pooled pair: the simulation arrays and the GPU-side mesh, both sized to a power of two. */
class Slot {
  readonly pool: ParticlePool;
  readonly mesh: Mesh<InstancedBufferGeometry, ShaderMaterial>;
  readonly buffer: InstancedInterleavedBuffer;
  private order: Uint32Array;
  private depth: Float32Array;

  constructor(readonly capacity: number) {
    this.pool = new ParticlePool(capacity);
    const geometry = new InstancedBufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(new Float32Array([-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0]), 3));
    geometry.setAttribute('uv', new BufferAttribute(new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]), 2));
    geometry.setIndex([0, 1, 2, 0, 2, 3]);
    this.buffer = new InstancedInterleavedBuffer(new Float32Array(capacity * FLOATS), FLOATS);
    this.buffer.setUsage(DynamicDrawUsage);
    geometry.setAttribute('iOffset', new InterleavedBufferAttribute(this.buffer, 3, 0));
    geometry.setAttribute('iColor', new InterleavedBufferAttribute(this.buffer, 4, 3));
    geometry.setAttribute('iSize', new InterleavedBufferAttribute(this.buffer, 2, 7));
    geometry.setAttribute('iVelocity', new InterleavedBufferAttribute(this.buffer, 3, 9));
    geometry.instanceCount = 0;
    const material = new ShaderMaterial({
      uniforms: { map: { value: particleTexture('dot') }, stretch: { value: 0 } },
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      transparent: true,
      depthWrite: false,
      side: DoubleSide,
      blending: AdditiveBlending,
    });
    this.mesh = new Mesh(geometry, material);
    this.mesh.name = 'gs_particles';
    this.mesh.frustumCulled = false; // Instances are not in the quad's bounds.
    this.mesh.castShadow = false;
    this.mesh.receiveShadow = false;
    this.mesh.matrixAutoUpdate = true;
    this.order = new Uint32Array(capacity);
    this.depth = new Float32Array(capacity);
  }

  /**
   * Writes the live particles into the instance buffer, relative to `origin`. With a camera, they are
   * sorted back to front (alpha blending); `toWorld` maps particle positions to world space when they
   * are local to the emitter.
   */
  write(s: ParticleSettings, origin: Vector3, camera: Camera | null, toWorld: Matrix4 | null): void {
    const pool = this.pool;
    const n = pool.count;
    const data = this.buffer.array as Float32Array;
    const order = this.order;
    for (let i = 0; i < n; i++) order[i] = i;
    if (camera && n > 1) {
      camera.updateMatrixWorld();
      const e = camera.matrixWorld.elements;
      // Distance along the camera's forward axis (-Z); only the order matters.
      const fx = -e[8]!;
      const fy = -e[9]!;
      const fz = -e[10]!;
      const depth = this.depth;
      const m = toWorld?.elements;
      for (let i = 0; i < n; i++) {
        let x = pool.pos[i * 3]!;
        let y = pool.pos[i * 3 + 1]!;
        let z = pool.pos[i * 3 + 2]!;
        if (m) {
          const wx = m[0]! * x + m[4]! * y + m[8]! * z + m[12]!;
          const wy = m[1]! * x + m[5]! * y + m[9]! * z + m[13]!;
          z = m[2]! * x + m[6]! * y + m[10]! * z + m[14]!;
          x = wx;
          y = wy;
        }
        depth[i] = x * fx + y * fy + z * fz;
      }
      order.subarray(0, n).sort((a, b) => depth[b]! - depth[a]!);
    }
    const bright = s.brightness;
    for (let k = 0; k < n; k++) {
      const i = order[k]!;
      const t = Math.min(1, pool.age[i]! / pool.life[i]!);
      const o = k * FLOATS;
      const p = i * 3;
      const c = i * 8;
      data[o] = pool.pos[p]! - origin.x;
      data[o + 1] = pool.pos[p + 1]! - origin.y;
      data[o + 2] = pool.pos[p + 2]! - origin.z;
      data[o + 3] = (pool.color[c]! + (pool.color[c + 4]! - pool.color[c]!) * t) * bright;
      data[o + 4] = (pool.color[c + 1]! + (pool.color[c + 5]! - pool.color[c + 1]!) * t) * bright;
      data[o + 5] = (pool.color[c + 2]! + (pool.color[c + 6]! - pool.color[c + 2]!) * t) * bright;
      data[o + 6] = Math.max(0, Math.min(1, lifeAlpha(t, pool.color[c + 3]!, pool.color[c + 7]!)));
      data[o + 7] = Math.max(0, pool.size[i * 2]! + (pool.size[i * 2 + 1]! - pool.size[i * 2]!) * t);
      data[o + 8] = pool.rot[i * 2]!;
      data[o + 9] = pool.vel[p]!;
      data[o + 10] = pool.vel[p + 1]!;
      data[o + 11] = pool.vel[p + 2]!;
    }
    this.mesh.geometry.instanceCount = n;
    this.buffer.clearUpdateRanges();
    if (n > 0) {
      this.buffer.addUpdateRange(0, n * FLOATS);
      this.buffer.needsUpdate = true;
    }
  }

  dispose(): void {
    this.mesh.removeFromParent();
    this.mesh.geometry.dispose();
    this.mesh.material.dispose();
  }
}

/** One emitter: an entity's ParticleEmitter, or one layer of a burst. */
interface Instance {
  core: EmitterCore;
  slot: Slot;
  /** The entity whose ParticleEmitter this is, or that a burst follows. */
  entity: Entity | null;
  /** Settings come from the entity's ParticleEmitter (read every step, so edits apply live). */
  component: boolean;
  settings: ParticleSettings;
  /** World matrix of a burst that does not follow anything. */
  origin: Matrix4 | null;
  /** Emission ended for good (component removed, entity destroyed). */
  orphan: boolean;
  /** A burst: released once it finished. */
  transient: boolean;
  /** Draw order among effects at the same spot (a preset's layers draw in their order). */
  layer: number;
  /** Component values that react to edits. */
  seenPlayOnStart: boolean;
  /** World position of the emitter at the last step (where world-space meshes sit). */
  readonly center: Vector3;
}

const tmpMatrix = new Matrix4();
const tmpQuat = new Quaternion();
const tmpScale = new Vector3();
const tmpGravity = new Vector3();
const inverse = new Quaternion();
const ZERO = new Vector3();
const color = new ThreeColor();

function nextPow2(n: number): number {
  let p = 16;
  while (p < n) p *= 2;
  return p;
}

/** A CSS colour (sRGB) or linear RGBA, as linear RGBA. */
export function toLinearColor(value: string | Color, alpha = 1): Color {
  if (typeof value === 'string') {
    color.setStyle(value);
    return [color.r, color.g, color.b, alpha];
  }
  return [value[0], value[1], value[2], value[3] ?? alpha];
}

export class VfxService implements VfxAPI {
  readonly presets = VFX_PRESETS;

  private world: World | null = null;
  private unsubscribe: (() => void) | null = null;
  private readonly instances: Instance[] = [];
  private readonly byEntity = new Map<Entity, Instance>();
  private readonly spare = new Map<number, Slot[]>();
  private readonly images = new Map<string, Texture>();
  private readonly loaded = new Set<Texture>();
  private readonly warned = new Set<string>();
  private assetUrl: (path: string) => string = (path) => path;
  private bursts = 0;

  constructor(private readonly warn: (message: string) => void = (m) => console.warn(m)) {}

  /**
   * Starts a new scene: drops every emitter and burst except the emitters of `keep` (entities
   * carried into the scene), whose particles carry on.
   */
  reset(world: World, assetUrl: (path: string) => string, keep: ReadonlySet<Entity> = new Set()): void {
    this.unsubscribe?.();
    this.world = world;
    this.assetUrl = assetUrl;
    this.bursts = 0;
    for (const instance of [...this.instances]) {
      if (!instance.entity || !keep.has(instance.entity) || instance.orphan) this.release(instance);
    }
    this.unsubscribe = world.onChange((event) => {
      if (event.type === 'destroy') this.entityGone(event.entity);
      else if (event.type === 'remove' && event.component === componentName(ParticleEmitter)) {
        const instance = this.byEntity.get(event.entity);
        if (instance?.component) this.orphan(instance, true);
      }
    });
  }

  /** One fixed step: picks up new emitters, then moves and emits every particle. */
  step(dt: number, gravity: Vector3): void {
    if (this.world) {
      for (const entity of this.world.query(ParticleEmitter)) {
        const instance = this.byEntity.get(entity);
        if (!instance || !instance.component) this.attach(entity);
        else this.syncComponent(instance);
      }
    }
    for (const instance of [...this.instances]) {
      this.stepInstance(instance, dt, gravity);
      if ((instance.orphan || instance.transient) && instance.core.finished) this.release(instance);
    }
  }

  /** Writes particle buffers for this draw. Call right before rendering. */
  render(camera: Camera | null, kind: 'webgl' | 'webgpu' = 'webgl'): void {
    if (kind === 'webgpu' && this.instances.length > 0) {
      this.warnOnce('webgpu', '[vfx] Particles draw with the WebGL renderer (the default); with renderer "webgpu" they are simulated but not drawn');
      for (const instance of this.instances) instance.slot.mesh.visible = false;
      return;
    }
    for (const instance of this.instances) {
      const { slot, settings } = instance;
      const mesh = slot.mesh;
      const n = slot.pool.count;
      mesh.visible = n > 0;
      if (n === 0) continue;
      const local = this.isLocal(instance);
      if (local) {
        if (mesh.parent !== instance.entity!.object3d) instance.entity!.object3d.add(mesh);
        mesh.position.set(0, 0, 0);
      } else {
        const root = this.world?.root;
        if (root && mesh.parent !== root) root.add(mesh);
        mesh.position.copy(instance.center);
      }
      mesh.updateMatrixWorld();
      this.applyLook(slot, settings, instance.layer);
      const sort = settings.blending === 'alpha' ? camera : null;
      slot.write(settings, local ? ZERO : instance.center, sort, local ? mesh.matrixWorld : null);
    }
  }

  dispose(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    for (const instance of [...this.instances]) this.release(instance);
    for (const slots of this.spare.values()) for (const slot of slots) slot.dispose();
    this.spare.clear();
    for (const texture of this.images.values()) texture.dispose();
    this.images.clear();
    this.loaded.clear();
  }

  /** Emitters and particles now (for tests and stats). */
  stats(): { emitters: number; particles: number } {
    return { emitters: this.instances.length, particles: this.count() };
  }

  // --- VfxAPI ------------------------------------------------------------------

  burst(at: Entity | Vec3Input, effect?: VfxPreset | ParticleOptions, options: ParticleOptions = {}): VfxEffect {
    const opts: ParticleOptions = typeof effect === 'object' && effect !== null ? { ...effect, ...options } : { ...options };
    const presetName = typeof effect === 'string' ? effect : opts.preset;
    let layers: Array<Partial<ParticleSettings>> = [{}];
    if (presetName !== undefined) {
      const found = PRESET_LAYERS[presetName as VfxPreset];
      if (found) layers = found;
      else this.warnOnce(`preset:${presetName}`, `[vfx] Unknown preset "${presetName}"; presets: ${VFX_PRESETS.join(', ')}`);
    }
    const point = isVec3Input(at) ? toVector3(at) : null;
    const entity = point ? null : (at as Entity);
    let origin: Matrix4 | null = null;
    if (point) origin = new Matrix4().makeTranslation(point.x, point.y, point.z);
    else if (entity?.alive) {
      entity.object3d.updateWorldMatrix(true, false);
      origin = entity.object3d.matrixWorld.clone();
    } else origin = new Matrix4();
    const follow = !!opts.follow && !!entity?.alive;
    const created: Instance[] = [];
    for (const [index, layer] of layers.entries()) {
      const settings = this.resolve(layer, opts);
      if (settings.image) this.image(settings.image); // Start loading now, not at the first draw.
      const capacity = Math.max(settings.maxParticles, settings.burst + 1);
      const seed = hashSeed('burst', this.bursts++, settings.seed);
      const instance = this.create(follow ? entity : null, false, settings, capacity, seed);
      instance.origin = follow ? null : origin;
      instance.layer = index;
      instance.core.play();
      this.stepInstance(instance, 0, ZERO);
      created.push(instance);
    }
    return {
      get alive() {
        return created.some((i) => !i.core.finished);
      },
      get count() {
        return created.reduce((n, i) => n + i.core.pool.count, 0);
      },
      stop: () => {
        for (const instance of created) instance.core.stop();
      },
    };
  }

  play(entity: Entity): void {
    this.componentInstance(entity, 'play')?.core.play();
  }

  stop(entity: Entity, options: { clear?: boolean } = {}): void {
    const instance = this.byEntity.get(entity);
    if (!instance) return;
    instance.core.stop();
    if (options.clear) instance.core.pool.clear();
  }

  emit(entity: Entity, count: number): void {
    const instance = this.componentInstance(entity, 'emit');
    if (!instance) return;
    instance.core.emit(count, instance.settings, this.frameOf(instance));
  }

  isPlaying(entity: Entity): boolean {
    return this.byEntity.get(entity)?.core.playing ?? false;
  }

  count(target?: Entity | VfxEffect): number {
    if (target === undefined) return this.instances.reduce((n, i) => n + i.core.pool.count, 0);
    if ('object3d' in target) return this.byEntity.get(target)?.core.pool.count ?? 0;
    return target.count;
  }

  clear(): void {
    for (const instance of [...this.instances]) {
      if (instance.component && !instance.orphan) instance.core.pool.clear();
      else this.release(instance);
    }
  }

  // --- Internals ----------------------------------------------------------------

  private isLocal(instance: Instance): boolean {
    return instance.settings.space === 'local' && !!instance.entity?.alive && !instance.orphan;
  }

  /** Full settings for a burst layer: burst defaults, the layer, then the caller's options. */
  private resolve(layer: Partial<ParticleSettings>, opts: ParticleOptions): ParticleSettings {
    const { preset: _p, scale = 1, amount = 1, color: tint, colorStart, colorEnd, palette, follow: _f, ...fields } = opts;
    const values: Record<string, unknown> = { loop: false, rate: 0, burst: 20, duration: 0.1, ...layer, ...fields };
    if (colorStart !== undefined) values.colorStart = toLinearColor(colorStart);
    if (colorEnd !== undefined) values.colorEnd = toLinearColor(colorEnd, 0);
    const s = fillDefaults(ParticleEmitter, values) as ParticleSettings;
    if (tint !== undefined) {
      const c = toLinearColor(tint);
      s.colorStart = [c[0], c[1], c[2], s.colorStart[3]];
      s.colorEnd = [c[0], c[1], c[2], s.colorEnd[3]];
    }
    const colors = palette ?? (layer.palette as Color[] | undefined);
    if (colors && colors.length > 0) s.palette = colors.map((c) => toLinearColor(c));
    if (scale !== 1) {
      s.size = [s.size[0] * scale, s.size[1] * scale];
      s.speed = [s.speed[0] * scale, s.speed[1] * scale];
      s.radius *= scale;
      s.box = [s.box[0] * scale, s.box[1] * scale, s.box[2] * scale];
    }
    if (amount !== 1) {
      s.burst = Math.round(s.burst * amount);
      s.rate *= amount;
      s.maxParticles = Math.max(s.maxParticles, Math.ceil(s.maxParticles * amount));
    }
    return s;
  }

  private create(entity: Entity | null, component: boolean, settings: ParticleSettings, capacity: number, seed: number): Instance {
    const slot = this.takeSlot(capacity);
    slot.pool.resize(capacity);
    slot.pool.clear();
    const instance: Instance = {
      core: new EmitterCore(slot.pool, mulberry32(seed)),
      slot,
      entity,
      component,
      settings,
      origin: null,
      orphan: false,
      transient: !component,
      layer: 0,
      seenPlayOnStart: settings.playOnStart,
      center: new Vector3(),
    };
    this.instances.push(instance);
    if (component && entity) this.byEntity.set(entity, instance);
    return instance;
  }

  private attach(entity: Entity): void {
    const settings = entity.get<ParticleSettings>(ParticleEmitter);
    if (settings.image) this.image(settings.image);
    const previous = this.byEntity.get(entity);
    if (previous) this.orphan(previous, true);
    const instance = this.create(entity, true, settings, settings.maxParticles, hashSeed('vfx', entity.id, settings.seed));
    if (settings.playOnStart) instance.core.play();
  }

  /** Follows edits of the component: new data object, capacity, playOnStart toggles. */
  private syncComponent(instance: Instance): void {
    const data = instance.entity!.tryGet<ParticleSettings>(ParticleEmitter);
    if (!data) return;
    instance.settings = data;
    if (data.maxParticles !== instance.slot.pool.capacity) this.resizeInstance(instance, data.maxParticles);
    if (data.playOnStart !== instance.seenPlayOnStart) {
      instance.seenPlayOnStart = data.playOnStart;
      if (data.playOnStart) instance.core.play();
      else instance.core.stop();
    }
  }

  private resizeInstance(instance: Instance, capacity: number): void {
    const old = instance.slot;
    if (nextPow2(capacity) === old.capacity) {
      old.pool.resize(capacity);
      return;
    }
    const slot = this.takeSlot(capacity);
    slot.pool.resize(capacity);
    slot.pool.clear();
    const keep = Math.min(old.pool.count, capacity);
    for (const key of ['pos', 'vel', 'age', 'life', 'size', 'rot', 'color'] as const) {
      const stride = old.pool[key].length / old.pool.capacity;
      slot.pool[key].set(old.pool[key].subarray(0, keep * stride));
    }
    slot.pool.count = keep;
    instance.core.pool = slot.pool; // The core keeps its random stream and cycle.
    this.returnSlot(old);
    instance.slot = slot;
  }

  private componentInstance(entity: Entity, what: string): Instance | null {
    if (!entity?.alive) return null;
    let instance = this.byEntity.get(entity);
    if (!instance && entity.has(ParticleEmitter)) {
      this.attach(entity);
      instance = this.byEntity.get(entity);
      instance?.core.stop();
    }
    if (!instance) {
      this.warnOnce(`nocomp:${entity.id}`, `[vfx] ctx.vfx.${what}: "${entity.name}" has no ParticleEmitter; use ctx.vfx.burst for one-off effects`);
      return null;
    }
    return instance;
  }

  private frameOf(instance: Instance): EmitFrame {
    if (this.isLocal(instance)) return { matrix: null, rotation: null };
    let matrix: Matrix4;
    if (instance.entity?.alive && !instance.orphan) {
      instance.entity.object3d.updateWorldMatrix(true, false);
      matrix = instance.entity.object3d.matrixWorld;
    } else matrix = instance.origin ?? tmpMatrix.identity();
    matrix.decompose(instance.center, tmpQuat, tmpScale);
    return { matrix, rotation: tmpQuat };
  }

  private stepInstance(instance: Instance, dt: number, gravity: Vector3): void {
    const frame = this.frameOf(instance);
    let g = gravity;
    if (this.isLocal(instance)) {
      // Particles live in the emitter's space: bring gravity into it.
      instance.entity!.object3d.getWorldQuaternion(inverse).invert();
      g = tmpGravity.copy(gravity).applyQuaternion(inverse);
    }
    instance.core.step(dt, instance.settings, frame, g);
  }

  /** Stops emission for good; world-space particles finish where they are, local ones go with the entity. */
  private orphan(instance: Instance, keepParticles: boolean): void {
    if (instance.entity && this.byEntity.get(instance.entity) === instance) this.byEntity.delete(instance.entity);
    if (instance.settings.space === 'local' || !keepParticles) {
      this.release(instance);
      return;
    }
    if (instance.entity?.alive) {
      instance.entity.object3d.updateWorldMatrix(true, false);
      instance.origin = instance.entity.object3d.matrixWorld.clone();
    } else instance.origin = new Matrix4().makeTranslation(instance.center.x, instance.center.y, instance.center.z);
    instance.settings = { ...instance.settings };
    instance.orphan = true;
    instance.core.stop();
  }

  private entityGone(entity: Entity): void {
    for (const instance of [...this.instances]) {
      if (instance.entity !== entity) continue;
      if (instance.component) this.orphan(instance, true);
      else {
        instance.origin = new Matrix4().makeTranslation(instance.center.x, instance.center.y, instance.center.z);
        instance.entity = null;
        instance.core.stop();
        instance.orphan = true;
      }
    }
  }

  private release(instance: Instance): void {
    const index = this.instances.indexOf(instance);
    if (index >= 0) this.instances.splice(index, 1);
    if (instance.entity && this.byEntity.get(instance.entity) === instance) this.byEntity.delete(instance.entity);
    instance.core.stop();
    this.returnSlot(instance.slot);
  }

  private takeSlot(capacity: number): Slot {
    const size = nextPow2(capacity);
    const slot = this.spare.get(size)?.pop() ?? new Slot(size);
    return slot;
  }

  private returnSlot(slot: Slot): void {
    slot.mesh.removeFromParent();
    slot.pool.clear();
    slot.mesh.geometry.instanceCount = 0;
    let list = this.spare.get(slot.capacity);
    if (!list) this.spare.set(slot.capacity, (list = []));
    list.push(slot);
  }

  /** Texture, blending and stretch of the slot's material for these settings. */
  private applyLook(slot: Slot, s: ParticleSettings, layer: number): void {
    const material = slot.mesh.material;
    // An image draws once it has loaded; the built-in sprite stands in until then.
    const image = s.image ? this.image(s.image) : null;
    const map = image && this.loaded.has(image) ? image : particleTexture(PARTICLE_TEXTURES.includes(s.texture as ParticleTexture) ? s.texture : 'dot');
    if (material.uniforms.map!.value !== map) material.uniforms.map!.value = map;
    material.uniforms.stretch!.value = Math.max(0, s.stretch);
    const blending = s.blending === 'alpha' ? NormalBlending : AdditiveBlending;
    if (material.blending !== blending) {
      material.blending = blending;
      material.needsUpdate = true;
    }
    // Additive glow draws over smoke and dust at the same spot; layers of a preset in their order.
    slot.mesh.renderOrder = (s.blending === 'alpha' ? 0 : 100) + layer;
  }

  private image(path: string): Texture {
    const url = this.assetUrl(path);
    let texture = this.images.get(url);
    if (!texture) {
      texture = new TextureLoader().load(
        url,
        (done) => void this.loaded.add(done),
        undefined,
        () => this.warnOnce(`image:${url}`, `[vfx] Could not load the particle image "${path}" (${url})`),
      );
      texture.colorSpace = SRGBColorSpace;
      this.images.set(url, texture);
    }
    return texture;
  }

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.warn(message);
  }
}
