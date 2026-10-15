// Navigation meshes and pathfinding (`ctx.nav`), on Recast/Detour (recast-navigation, WASM).
//
// The navmesh is built in the browser when a scene loads, from the same colliders the physics uses
// (or from the meshes under the NavMesh object), so prefab instances, GPU instances, collision-only
// proxies and live edits from Blender are all in it. The game waits for the build before the scene
// starts, so a playtest's step 0 already has it. Recast is loaded (dynamic import, ~1 MB with its
// WASM) only by scenes that use navigation: a NavMesh or NavAgent in the level or its prefabs.
// Builds are cached by their input, so Stop and restarts reuse the mesh.

import type RAPIER from '@dimforge/rapier3d-compat';
import type { NavMesh as RecastNavMesh, NavMeshQuery } from 'recast-navigation';
import {
  BufferAttribute,
  BufferGeometry,
  Float32BufferAttribute,
  Group,
  LineBasicMaterial,
  LineSegments,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  Quaternion,
  Vector3,
  WireframeGeometry,
  type InstancedMesh,
  type Object3D,
  type Scene,
} from 'three';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { toVector3, type Vec3Input } from '../math';
import type { PhysicsAPI } from '../physics/backend';
import { Collider, NavAgent, NavMesh, RigidBody } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import type { SceneManifest } from '../scene/manifest';
import { isShaderPass } from '../scene/passes';

export type NavMeshSettings = ComponentValues<typeof NavMesh>;

export interface NavPathOptions {
  /** Unreachable goal: return the path to the closest point it can reach instead of `null`. Default false. */
  partial?: boolean;
  /** How far (metres, sideways) `from` and `to` may be from the navmesh. Default 2; up and down twice that. */
  range?: number;
}

export interface NavStats {
  ready: boolean;
  /** Triangles of the navmesh's polygons. */
  triangles: number;
  /** Milliseconds the last build took (0: from the cache). */
  buildMs: number;
  /** Entities with a NavAgent. */
  agents: number;
}

/** What systems use through `ctx.nav`. */
export interface NavAPI {
  /** A navmesh is built for this scene. */
  readonly ready: boolean;
  /** Draw the navmesh and the agents' paths (screenshots, debugging). */
  debug: boolean;
  /** The settings of the current navmesh (the scene's NavMesh component, else the defaults). */
  readonly settings: Readonly<NavMeshSettings>;
  /**
   * Waypoints (Y-up, on the navmesh) from `from` to `to`, both included, going around walls and props.
   * `null` when there is no navmesh, a point is off it, or the goal cannot be reached (see `partial`).
   */
  path(from: Vec3Input, to: Vec3Input, options?: NavPathOptions): Vector3[] | null;
  /** The closest point on the navmesh, or `null` when none is within `range` (default 2 m sideways). */
  closest(point: Vec3Input, options?: { range?: number }): Vector3 | null;
  /** Sends a NavAgent to a point: sets its `destination`, turns `useDestination` on and clears `target`. */
  moveTo(agent: Entity, point: Vec3Input): void;
  /** Stops a NavAgent: clears its `target` and `useDestination`. */
  stop(agent: Entity): void;
  /** A NavAgent's current waypoints (empty without a path). */
  agentPath(agent: Entity): readonly Vector3[];
  /**
   * Builds the navmesh again from the level now, e.g. after moving static geometry from code (spawned
   * and destroyed static colliders and live edits rebuild by themselves). Loads Recast first if needed.
   */
  rebuild(): Promise<boolean>;
  stats(): NavStats;
}

type Recast = typeof import('recast-navigation') & typeof import('recast-navigation/generators');

let recastLoad: Promise<Recast> | null = null;
let recast: Recast | null = null;

/** Loads and initializes Recast (WASM) once per page or process. */
export function loadRecast(): Promise<Recast> {
  return (recastLoad ??= Promise.all([import('recast-navigation'), import('recast-navigation/generators')]).then(
    async ([core, generators]) => {
      await core.init();
      return (recast = { ...core, ...generators });
    },
  ));
}

/** The scene wants navigation: a NavMesh or a NavAgent in it or in one of its prefabs. */
export function sceneUsesNav(manifest: Pick<SceneManifest, 'entities' | 'prefabs'>): boolean {
  const uses = (entities: SceneManifest['entities']): boolean =>
    entities.some((e) => e.components[NavMesh.name] !== undefined || e.components[NavAgent.name] !== undefined);
  return uses(manifest.entities) || manifest.prefabs.some((p) => uses(p.entities));
}

/** Agents' waypoints, kept by the NavAgent system (agent.ts) and read for `agentPath` and the debug view. */
export const agentPaths = new WeakMap<Entity, Vector3[]>();

const DEFAULTS: NavMeshSettings = {
  agentRadius: 0.4,
  agentHeight: 1.8,
  maxClimb: 0.35,
  maxSlope: Math.PI / 4,
  cellSize: 0.2,
  source: 'colliders',
};
/** Most cells a navmesh grid may have (2000 × 2000); bigger levels get a coarser cell size. */
const MAX_CELLS = 4_000_000;
const CACHE_SIZE = 4;
const MAX_PATH_POLYS = 1024;
const MAX_PATH_POINTS = 256;
const DEBUG_LIFT = 0.04;

interface Built {
  navMesh: RecastNavMesh;
  query: NavMeshQuery;
  triangles: number;
}

/** Triangles in world space (Y-up). */
interface Soup {
  positions: number[];
  indices: number[];
}

export class NavService implements NavAPI {
  private world: World | null = null;
  private physics: PhysicsAPI | null = null;
  private scene: Scene | null = null;
  private unsubscribe: (() => void) | null = null;
  private wanted = false;
  private dirty = false;
  private current: Built | null = null;
  private currentKey = 0;
  private readonly cache = new Map<number, Built>();
  private lastBuildMs = 0;
  private resolved: NavMeshSettings = { ...DEFAULTS };
  private debugOn = false;
  private debugGroup: Group | null = null;
  private warned = new Set<string>();

  constructor(private readonly warn: (message: string) => void = (m) => console.warn(`[gamestudio] ${m}`)) {}

  get ready(): boolean {
    return this.current !== null;
  }

  get settings(): Readonly<NavMeshSettings> {
    return this.resolved;
  }

  get debug(): boolean {
    return this.debugOn;
  }

  set debug(on: boolean) {
    this.debugOn = !!on;
    this.refreshDebug();
  }

  /**
   * A new scene (load or reset): forgets the old navmesh and, when `wanted`, loads Recast and builds
   * the new one before returning. `scene` is the Three.js scene for the debug view.
   */
  async reset(world: World, physics: PhysicsAPI, scene: Scene, wanted: boolean): Promise<void> {
    this.unsubscribe?.();
    this.world = world;
    this.physics = physics;
    this.scene = scene;
    this.current = null;
    this.currentKey = 0;
    this.dirty = false;
    // Also for entities kept from the last scene (ctx.loadScene keep) that walk or hold the settings.
    this.wanted = wanted || world.query(NavMesh).length > 0 || world.query(NavAgent).length > 0;
    this.unsubscribe = world.onChange((event) => {
      if ((event.type !== 'add' && event.type !== 'remove') || !this.wanted) return;
      if (event.component === NavMesh.name || event.component === Collider.name || event.component === RigidBody.name) this.dirty = true;
    });
    if (this.wanted) {
      try {
        await loadRecast();
        this.build();
      } catch (error) {
        this.warn(`navigation: cannot load Recast: ${error instanceof Error ? error.message : error}`);
      }
    }
    this.refreshDebug();
  }

  /** Static geometry moved (a live edit): rebuild before the next query. */
  touch(): void {
    if (this.wanted) this.dirty = true;
  }

  async rebuild(): Promise<boolean> {
    if (!this.world) return false;
    this.wanted = true;
    if (!recast) await loadRecast();
    return this.build();
  }

  path(from: Vec3Input, to: Vec3Input, options: NavPathOptions = {}): Vector3[] | null {
    const built = this.use();
    if (!built || !recast) return null;
    const half = extents(options.range);
    const { query } = built;
    const a = toVector3(from);
    const b = toVector3(to);
    const start = query.findNearestPoly(a, { halfExtents: half });
    const end = query.findNearestPoly(b, { halfExtents: half });
    if (!start.success || !end.success || start.nearestRef === 0 || end.nearestRef === 0) return null;
    const startPoint = start.nearestPoint;
    let endPoint = end.nearestPoint;
    const found = query.findPath(start.nearestRef, end.nearestRef, startPoint, endPoint, { maxPathPolys: MAX_PATH_POLYS });
    try {
      if (!found.success || found.polys.size <= 0) return null;
      const last = found.polys.get(found.polys.size - 1);
      if (last !== end.nearestRef) {
        if (!options.partial) return null; // another island, or behind a closed door
        const closest = query.closestPointOnPoly(last, endPoint);
        if (!closest.success) return null;
        endPoint = closest.closestPoint;
      }
      const straight = query.findStraightPath(startPoint, endPoint, found.polys, { maxStraightPathPoints: MAX_PATH_POINTS });
      try {
        if (!straight.success || straight.straightPathCount === 0) return null;
        const points: Vector3[] = [];
        for (let i = 0; i < straight.straightPathCount; i++) {
          points.push(new Vector3(straight.straightPath.get(i * 3), straight.straightPath.get(i * 3 + 1), straight.straightPath.get(i * 3 + 2)));
        }
        return points;
      } finally {
        straight.straightPath.destroy();
        straight.straightPathFlags.destroy();
        straight.straightPathRefs.destroy();
      }
    } finally {
      found.polys.destroy();
    }
  }

  closest(point: Vec3Input, options: { range?: number } = {}): Vector3 | null {
    const built = this.use();
    if (!built) return null;
    const hit = built.query.findNearestPoly(toVector3(point), { halfExtents: extents(options.range) });
    if (!hit.success || hit.nearestRef === 0) return null;
    return new Vector3(hit.nearestPoint.x, hit.nearestPoint.y, hit.nearestPoint.z);
  }

  moveTo(agent: Entity, point: Vec3Input): void {
    const a = agent.tryGet<ComponentValues<typeof NavAgent>>(NavAgent);
    if (!a) return this.warnOnce(`moveTo: "${agent.name}" has no NavAgent`);
    const p = toVector3(point);
    a.destination = [p.x, p.y, p.z];
    a.useDestination = true;
    a.target = null;
  }

  stop(agent: Entity): void {
    const a = agent.tryGet<ComponentValues<typeof NavAgent>>(NavAgent);
    if (!a) return;
    a.target = null;
    a.useDestination = false;
  }

  agentPath(agent: Entity): readonly Vector3[] {
    return agentPaths.get(agent) ?? [];
  }

  stats(): NavStats {
    return {
      ready: this.ready,
      triangles: this.current?.triangles ?? 0,
      buildMs: Math.round(this.lastBuildMs * 10) / 10,
      agents: this.world?.query(NavAgent).length ?? 0,
    };
  }

  dispose(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.removeDebug();
    for (const built of this.cache.values()) destroyBuilt(built);
    this.cache.clear();
    this.current = null;
  }

  // ---------------------------------------------------------------------------

  private warnOnce(message: string): void {
    if (this.warned.has(message)) return;
    this.warned.add(message);
    this.warn(`navigation: ${message}`);
  }

  /** The navmesh to query, rebuilt first when static geometry changed. */
  private use(): Built | null {
    if (this.dirty && recast) this.build();
    return this.current;
  }

  /** Builds (or takes from the cache) the navmesh of the current level. Synchronous once Recast is loaded. */
  private build(): boolean {
    const world = this.world;
    const physics = this.physics;
    this.dirty = false;
    if (!world || !physics || !recast) return false;
    const holder = world.query(NavMesh)[0];
    const settings: NavMeshSettings = { ...DEFAULTS, ...(holder?.get(NavMesh) ?? {}) };
    this.resolved = settings;
    const soup: Soup = { positions: [], indices: [] };
    if (holder && settings.source === 'meshes') addMeshes(soup, holder.object3d);
    else addColliders(soup, world, physics);
    if (soup.indices.length === 0) {
      this.warnOnce('nothing to walk on: no solid fixed Collider in the level (or no mesh under the NavMesh object)');
      this.current = null;
      this.refreshDebug();
      return false;
    }
    const config = recastConfig(settings, soup.positions);
    const key = hashInput(soup, Object.values(config));
    if (key === this.currentKey && this.current) return true;
    const cached = this.cache.get(key);
    if (cached) {
      this.cache.delete(key);
      this.cache.set(key, cached); // most recently used last
      this.current = cached;
      this.currentKey = key;
      this.lastBuildMs = 0;
      this.refreshDebug();
      return true;
    }
    const started = performance.now();
    const result = recast.generateSoloNavMesh(soup.positions, soup.indices, config);
    if (!result.success) {
      this.warnOnce(`the navmesh build failed: ${result.error}`);
      this.current = null;
      this.refreshDebug();
      return false;
    }
    const query = new recast.NavMeshQuery(result.navMesh, { maxNodes: 4096 });
    const [, indices] = recast.getNavMeshPositionsAndIndices(result.navMesh);
    const built: Built = { navMesh: result.navMesh, query, triangles: indices.length / 3 };
    this.lastBuildMs = performance.now() - started;
    this.cache.set(key, built);
    while (this.cache.size > CACHE_SIZE) {
      const [oldest, old] = this.cache.entries().next().value!;
      this.cache.delete(oldest);
      if (old !== built) destroyBuilt(old);
    }
    this.current = built;
    this.currentKey = key;
    this.refreshDebug();
    return true;
  }

  private removeDebug(): void {
    const group = this.debugGroup;
    if (!group) return;
    group.removeFromParent();
    group.traverse((o) => {
      const m = o as Mesh;
      m.geometry?.dispose();
      (m.material as MeshBasicMaterial | undefined)?.dispose();
    });
    this.debugGroup = null;
  }

  /** The debug view: the navmesh (translucent, with its edges) and every agent's path, drawn on top. */
  private refreshDebug(): void {
    this.removeDebug();
    if (!this.debugOn || !this.scene || !recast) return;
    const group = new Group();
    group.name = 'gs_nav_debug';
    if (this.current) {
      const [positions, indices] = recast.getNavMeshPositionsAndIndices(this.current.navMesh);
      const geometry = new BufferGeometry();
      geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
      geometry.setIndex(indices);
      geometry.translate(0, DEBUG_LIFT, 0);
      const fill = new Mesh(geometry, new MeshBasicMaterial({ color: 0x22c3ff, transparent: true, opacity: 0.35, depthWrite: false }));
      const edges = new LineSegments(new WireframeGeometry(geometry), new LineBasicMaterial({ color: 0x0a6cff, transparent: true, opacity: 0.8 }));
      fill.renderOrder = edges.renderOrder = 998;
      group.add(fill, edges);
    }
    const lines = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ color: 0xffd400, depthTest: false }));
    lines.renderOrder = 999;
    lines.frustumCulled = false;
    lines.onBeforeRender = () => this.drawAgentPaths(lines);
    group.add(lines);
    this.scene.add(group);
    this.debugGroup = group;
  }

  private drawAgentPaths(lines: LineSegments): void {
    const points: number[] = [];
    for (const agent of this.world?.query(NavAgent) ?? []) {
      const path = agentPaths.get(agent);
      if (!path || path.length === 0) continue;
      let from = agent.object3d.position;
      for (const p of path) {
        points.push(from.x, from.y + DEBUG_LIFT * 2, from.z, p.x, p.y + DEBUG_LIFT * 2, p.z);
        from = p;
      }
    }
    const geometry = lines.geometry as BufferGeometry;
    const old = geometry.getAttribute('position') as BufferAttribute | undefined;
    if (old && old.array.length === points.length) {
      (old.array as Float32Array).set(points);
      old.needsUpdate = true;
    } else {
      geometry.setAttribute('position', new Float32BufferAttribute(points, 3));
    }
    geometry.setDrawRange(0, points.length / 3);
  }
}

function destroyBuilt(built: Built): void {
  built.query.destroy();
  built.navMesh.destroy();
}

function extents(range = 2): { x: number; y: number; z: number } {
  const r = Math.max(0.05, range);
  return { x: r, y: r * 2, z: r };
}

/** Recast's settings (in cells) from the NavMesh component's (in metres and radians). */
function recastConfig(s: NavMeshSettings, positions: number[]) {
  let cs = Math.max(0.05, s.cellSize);
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < positions.length; i += 3) {
    minX = Math.min(minX, positions[i]!);
    maxX = Math.max(maxX, positions[i]!);
    minZ = Math.min(minZ, positions[i + 2]!);
    maxZ = Math.max(maxZ, positions[i + 2]!);
  }
  const cells = ((maxX - minX) / cs) * ((maxZ - minZ) / cs);
  if (cells > MAX_CELLS) cs = Math.ceil(cs * Math.sqrt(cells / MAX_CELLS) * 100) / 100;
  const ch = cs / 2;
  return {
    cs,
    ch,
    walkableSlopeAngle: Math.min(89.9, Math.max(0, (s.maxSlope * 180) / Math.PI)),
    walkableHeight: Math.max(3, Math.ceil(s.agentHeight / ch)),
    walkableClimb: Math.max(0, Math.floor(s.maxClimb / ch)),
    walkableRadius: Math.max(0, Math.ceil(s.agentRadius / cs)),
    maxEdgeLen: Math.round(12 / cs),
    maxSimplificationError: 1.3,
    minRegionArea: 8, // islands smaller than this many cells are dropped
    mergeRegionArea: 20,
    maxVertsPerPoly: 6,
    detailSampleDist: cs * 6,
    detailSampleMaxError: ch,
  };
}

/** FNV-1a over the triangles and the settings: the cache key, and the same on every load. */
function hashInput(soup: Soup, settings: unknown[]): number {
  let h = 0x811c9dc5;
  const f32 = new Float32Array(1);
  const u32 = new Uint32Array(f32.buffer);
  const mix = (n: number): void => {
    h = Math.imul(h ^ n, 0x01000193) >>> 0;
  };
  for (const p of soup.positions) {
    f32[0] = p;
    mix(u32[0]!);
  }
  for (const i of soup.indices) mix(i);
  for (const value of settings) {
    f32[0] = Number(value);
    mix(u32[0]!);
  }
  return h;
}

// --- Input geometry ---------------------------------------------------------------------------------

const tmpPos = new Vector3();
const tmpQuat = new Quaternion();
const tmpScale = new Vector3();
const tmpVec = new Vector3();
const UNIT = new Vector3(1, 1, 1);

/** Every solid fixed collider (no RigidBody, or a fixed one), in world space, in entity order. */
function addColliders(soup: Soup, world: World, physics: PhysicsAPI): void {
  const R = physics.rapier;
  for (const entity of world.query(Collider)) {
    const col = entity.get<ComponentValues<typeof Collider>>(Collider);
    const rb = entity.tryGet<ComponentValues<typeof RigidBody>>(RigidBody);
    if (col.sensor || (rb && rb.type !== 'fixed')) continue;
    const collider = physics.colliderOf(entity);
    if (!collider) continue;
    // The object's world pose (as the body has it, without scale: shapes are sized with it), then
    // the collider's offset on the body. The object owns a fixed body's pose, so this is never stale.
    entity.object3d.updateWorldMatrix(true, false);
    entity.object3d.matrixWorld.decompose(tmpPos, tmpQuat, tmpScale);
    const body = new Matrix4().compose(tmpPos, tmpQuat, UNIT);
    const t = collider.translationWrtParent() ?? { x: 0, y: 0, z: 0 };
    const r = collider.rotationWrtParent() ?? { x: 0, y: 0, z: 0, w: 1 };
    const local = new Matrix4().compose(tmpVec.set(t.x, t.y, t.z), tmpQuat.set(r.x, r.y, r.z, r.w), UNIT);
    addShape(soup, R, collider.shape, body.multiply(local));
  }
}

function addShape(soup: Soup, R: PhysicsAPI['rapier'], shape: RAPIER.Shape, m: Matrix4): void {
  const T = R.ShapeType;
  switch (shape.type) {
    case T.Cuboid:
    case T.RoundCuboid: {
      const s = shape as RAPIER.Cuboid & { borderRadius?: number };
      const b = s.borderRadius ?? 0;
      addBox(soup, s.halfExtents.x + b, s.halfExtents.y + b, s.halfExtents.z + b, m);
      break;
    }
    case T.Ball:
      addPrism(soup, (shape as RAPIER.Ball).radius, (shape as RAPIER.Ball).radius, m);
      break;
    case T.Capsule: {
      const s = shape as RAPIER.Capsule;
      addPrism(soup, s.radius, s.halfHeight + s.radius, m);
      break;
    }
    case T.Cylinder:
    case T.Cone:
    case T.RoundCylinder:
    case T.RoundCone: {
      const s = shape as RAPIER.Cylinder;
      addPrism(soup, s.radius, s.halfHeight, m);
      break;
    }
    case T.TriMesh:
    case T.ConvexPolyhedron:
    case T.RoundConvexPolyhedron: {
      const s = shape as RAPIER.TriMesh;
      if (s.indices && s.indices.length >= 3) addTriangles(soup, s.vertices, s.indices, m);
      else addBounds(soup, s.vertices, m);
      break;
    }
    case T.Compound: {
      const s = shape as RAPIER.Compound;
      s.shapes.forEach((part, i) => {
        const p = s.positions[i] ?? { x: 0, y: 0, z: 0 };
        const q = s.rotations[i] ?? { x: 0, y: 0, z: 0, w: 1 };
        const local = new Matrix4().compose(tmpVec.set(p.x, p.y, p.z), tmpQuat.set(q.x, q.y, q.z, q.w), UNIT);
        addShape(soup, R, part, m.clone().multiply(local));
      });
      break;
    }
    default:
      break; // heightfields, polylines and voxels: the engine never makes them
  }
}

function addVertex(soup: Soup, x: number, y: number, z: number, m: Matrix4): number {
  tmpVec.set(x, y, z).applyMatrix4(m);
  soup.positions.push(tmpVec.x, tmpVec.y, tmpVec.z);
  return soup.positions.length / 3 - 1;
}

/** Quads (a, b, c, d) counter-clockwise seen from outside, so tops face up for Recast. */
function addQuads(soup: Soup, base: number, quads: number[][]): void {
  for (const [a, b, c, d] of quads) soup.indices.push(base + a!, base + b!, base + c!, base + a!, base + c!, base + d!);
}

function addBox(soup: Soup, hx: number, hy: number, hz: number, m: Matrix4): void {
  const base = soup.positions.length / 3;
  for (const [x, y, z] of [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]]) {
    addVertex(soup, x! * hx, y! * hy, z! * hz, m);
  }
  addQuads(soup, base, [[3, 7, 6, 2], [0, 1, 5, 4], [1, 2, 6, 5], [0, 4, 7, 3], [4, 5, 6, 7], [0, 3, 2, 1]]);
}

/** An upright octagonal prism: balls, capsules and cylinders (their footprint is what matters). */
function addPrism(soup: Soup, radius: number, halfHeight: number, m: Matrix4): void {
  const n = 8;
  const base = soup.positions.length / 3;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    addVertex(soup, Math.cos(a) * radius, -halfHeight, Math.sin(a) * radius, m); // base + 2i
    addVertex(soup, Math.cos(a) * radius, halfHeight, Math.sin(a) * radius, m); // base + 2i + 1
  }
  const bottom = addVertex(soup, 0, -halfHeight, 0, m);
  const top = addVertex(soup, 0, halfHeight, 0, m);
  for (let i = 0; i < n; i++) {
    const b0 = base + 2 * i;
    const b1 = base + 2 * ((i + 1) % n);
    soup.indices.push(b0, b0 + 1, b1 + 1, b0, b1 + 1, b1); // side, facing out
    soup.indices.push(top, b1 + 1, b0 + 1); // top, facing up
    soup.indices.push(bottom, b0, b1); // bottom, facing down
  }
}

function addTriangles(soup: Soup, vertices: ArrayLike<number>, indices: ArrayLike<number>, m: Matrix4): void {
  const base = soup.positions.length / 3;
  for (let i = 0; i + 2 < vertices.length; i += 3) addVertex(soup, vertices[i]!, vertices[i + 1]!, vertices[i + 2]!, m);
  for (let i = 0; i + 2 < indices.length; i += 3) soup.indices.push(base + indices[i]!, base + indices[i + 1]!, base + indices[i + 2]!);
}

/** The box around points (a hull without its triangles). */
function addBounds(soup: Soup, vertices: ArrayLike<number>, m: Matrix4): void {
  const min = new Vector3(Infinity, Infinity, Infinity);
  const max = new Vector3(-Infinity, -Infinity, -Infinity);
  for (let i = 0; i + 2 < vertices.length; i += 3) {
    tmpPos.set(vertices[i]!, vertices[i + 1]!, vertices[i + 2]!);
    min.min(tmpPos);
    max.max(tmpPos);
  }
  if (min.x > max.x) return;
  const centre = min.clone().add(max).multiplyScalar(0.5);
  addBox(soup, (max.x - min.x) / 2, (max.y - min.y) / 2, (max.z - min.z) / 2, m.clone().multiply(new Matrix4().makeTranslation(centre)));
}

/** The meshes under `root` (GPU instances copy by copy), in world space. */
function addMeshes(soup: Soup, root: Object3D): void {
  root.updateWorldMatrix(true, true);
  root.traverse((object) => {
    const mesh = object as Mesh;
    if (!mesh.isMesh || isShaderPass(mesh)) return; // outline hulls and x-ray passes repeat their mesh
    const geometry = mesh.geometry as BufferGeometry;
    const position = geometry.getAttribute('position');
    if (!position) return;
    const vertices = new Float32Array(position.count * 3);
    for (let i = 0; i < position.count; i++) {
      vertices[i * 3] = position.getX(i);
      vertices[i * 3 + 1] = position.getY(i);
      vertices[i * 3 + 2] = position.getZ(i);
    }
    const index = geometry.getIndex();
    const indices = index ? Array.from(index.array as ArrayLike<number>) : Array.from({ length: position.count }, (_, i) => i);
    const instanced = mesh as unknown as InstancedMesh;
    if (instanced.isInstancedMesh) {
      const each = new Matrix4();
      for (let i = 0; i < instanced.count; i++) {
        instanced.getMatrixAt(i, each);
        addTriangles(soup, vertices, indices, mesh.matrixWorld.clone().multiply(each));
      }
    } else {
      addTriangles(soup, vertices, indices, mesh.matrixWorld);
    }
  });
}
