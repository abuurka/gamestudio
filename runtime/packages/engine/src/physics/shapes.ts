// Collider sizing from mesh geometry (docs/SPEC.md §6.3).
// Shapes are computed in the entity object's local space, then multiplied by
// its world scale, because Rapier bodies carry no scale. GPU instances (an
// InstancedMesh, e.g. a geometry-nodes scatter) count copy by copy, each at its
// instance matrix.

import {
  Box3,
  Matrix4,
  Mesh,
  Quaternion,
  Vector3,
  type BufferGeometry,
  type InstancedMesh,
  type Object3D,
  type SkinnedMesh,
} from 'three';
import { isShaderPass } from '../scene/passes';

export type ShapeKind = 'auto' | 'box' | 'sphere' | 'capsule' | 'convex' | 'mesh';

/** One part of a compound shape, placed in the entity's (scaled) local space. */
export type PartSpec =
  | { kind: 'box'; halfExtents: Vector3; center: Vector3; rotation: Quaternion }
  | { kind: 'convex'; points: Float32Array; center: Vector3; rotation: Quaternion };

/** Shape description independent of the physics engine. */
export type ShapeSpec =
  | { kind: 'box'; halfExtents: Vector3; center: Vector3 }
  | { kind: 'sphere'; radius: number; center: Vector3 }
  | { kind: 'capsule'; halfHeight: number; radius: number; center: Vector3 }
  | { kind: 'convex'; points: Float32Array; center: Vector3 }
  | { kind: 'mesh'; vertices: Float32Array; indices: Uint32Array; center: Vector3 }
  | { kind: 'compound'; parts: PartSpec[]; center: Vector3 };

const MIN_HALF = 0.005;

/** The copies an InstancedMesh draws of one geometry, in root-local space. */
export interface InstancedGeometry {
  geometry: BufferGeometry;
  /** The geometry's bounds in its own space. */
  box: Box3;
  /** Instance space → root-local space, one matrix per drawn copy. */
  matrices: Matrix4[];
}

export interface LocalGeometry {
  /** Bounds in the object's local space, instances included (empty when there is no mesh). */
  box: Box3;
  /** Bounds of the plain (not instanced) meshes alone. */
  plainBox: Box3;
  /** Vertices and triangles of the plain meshes, root-local (when vertices were asked for). */
  positions: number[];
  indices: number[];
  /** GPU instances, one entry per InstancedMesh. */
  instanced: InstancedGeometry[];
}

/**
 * Gathers the meshes that belong to `root` itself, in root-local space,
 * skipping subtrees owned by other entities (`isOtherEntity`).
 */
export function collectLocalGeometry(
  root: Object3D,
  isOtherEntity: (object: Object3D) => boolean,
  withVertices: boolean,
): LocalGeometry {
  root.updateWorldMatrix(true, true);
  const plainBox = new Box3();
  const box = new Box3();
  const positions: number[] = [];
  const indices: number[] = [];
  const instanced: InstancedGeometry[] = [];
  const tmpBox = new Box3();
  const tmpMatrix = new Matrix4();
  const v = new Vector3();

  const visit = (object: Object3D, rel: Matrix4): void => {
    if (isShaderPass(object)) return; // an outline hull or x-ray pass repeats its mesh
    if (object instanceof Mesh && object.geometry) {
      const geometry = object.geometry;
      const copies = (object as InstancedMesh).isInstancedMesh ? (object as InstancedMesh) : null;
      // A skinned mesh's vertices are where its bones put them (exported skins often keep raw
      // positions in another space), so they are measured as drawn, vertex by vertex.
      const skin = !copies && (object as SkinnedMesh).isSkinnedMesh ? (object as SkinnedMesh) : null;
      if (copies) {
        if (!geometry.boundingBox) geometry.computeBoundingBox();
        const source = geometry.boundingBox!;
        const matrices: Matrix4[] = [];
        for (let i = 0; i < copies.count; i++) {
          copies.getMatrixAt(i, tmpMatrix);
          const matrix = rel.clone().multiply(tmpMatrix);
          matrices.push(matrix);
          if (!source.isEmpty()) box.union(tmpBox.copy(source).applyMatrix4(matrix));
        }
        if (matrices.length) instanced.push({ geometry, box: source.clone(), matrices });
      } else {
        if (skin) {
          for (const bone of skin.skeleton?.bones ?? []) bone.updateWorldMatrix(true, false);
          skin.updateMatrixWorld(); // refreshes bindMatrixInverse, which updateWorldMatrix leaves stale
        } else {
          if (!geometry.boundingBox) geometry.computeBoundingBox();
          plainBox.union(tmpBox.copy(geometry.boundingBox!).applyMatrix4(rel));
        }
        if (withVertices || skin) {
          const pos = geometry.getAttribute('position');
          const base = positions.length / 3;
          for (let i = 0; i < pos.count; i++) {
            if (skin) {
              skin.getVertexPosition(i, v).applyMatrix4(rel);
              plainBox.expandByPoint(v);
            } else {
              v.fromBufferAttribute(pos, i).applyMatrix4(rel);
            }
            if (withVertices) positions.push(v.x, v.y, v.z);
          }
          if (withVertices) {
            const index = geometry.getIndex();
            if (index) for (let i = 0; i < index.count; i++) indices.push(base + index.getX(i));
            else for (let i = 0; i < pos.count; i++) indices.push(base + i);
          }
        }
      }
    }
    for (const child of object.children) {
      if (isOtherEntity(child)) continue;
      visit(child, rel.clone().multiply(child.matrix));
    }
  };
  visit(root, new Matrix4());
  box.union(plainBox);
  return { box, plainBox, positions, indices, instanced };
}

/** What a child object is to a collider: not an entity root, a plain entity, or one with its own physics. */
export type ChildKind = 'none' | 'plain' | 'physics';

/**
 * Which child entities `root`'s collider leaves out (for `collectLocalGeometry`). Child entities
 * are left out (a lamp on a crate), except plain ones (no physics components) that are the body:
 * skinned meshes deformed by bones under `root` (a rigged character, without the props it holds),
 * and every plain child when `root` has no mesh of its own and no skinned body (an empty over its
 * meshes).
 */
export function colliderSkips(root: Object3D, kindOf: (object: Object3D) => ChildKind): (object: Object3D) => boolean {
  const isChildEntity = (o: Object3D): boolean => o !== root && kindOf(o) !== 'none';
  const hasOwnMesh = !collectLocalGeometry(root, isChildEntity, false).box.isEmpty();
  // A rigged character (no mesh of its own, a skinned body under it) is its skinned body: props it
  // holds (a gun parented to a hand) would make the capsule wider and push it off the body's axis.
  let skinnedBody = false;
  if (!hasOwnMesh) {
    root.traverse((o) => {
      if (!skinnedBody && o !== root && kindOf(o) === 'plain' && skinnedBy(o, root)) skinnedBody = true;
    });
  }
  return (o) => {
    if (o === root) return false;
    const kind = kindOf(o);
    if (kind === 'none') return false;
    if (kind === 'physics') return true;
    return (hasOwnMesh || skinnedBody) && !skinnedBy(o, root);
  };
}

/** True if a skinned mesh in `object`'s subtree is deformed by bones under `root`. */
function skinnedBy(object: Object3D, root: Object3D): boolean {
  let found = false;
  object.traverse((o) => {
    const bone = (o as SkinnedMesh).isSkinnedMesh ? (o as SkinnedMesh).skeleton?.bones[0] : undefined;
    for (let p = bone?.parent ?? null; p && !found; p = p.parent) found = p === root;
  });
  return found;
}

/**
 * Builds a shape for `kind` from local geometry scaled by `scale` (world scale). GPU instances
 * become one part each: boxes (`auto`/`box`) or hulls (`convex`) of a compound shape, or copies in
 * one trimesh (`mesh`); `sphere` and `capsule` enclose everything.
 */
export function shapeFromGeometry(kind: ShapeKind, geometry: LocalGeometry, scale: Vector3): ShapeSpec {
  if (geometry.instanced.length && kind !== 'sphere' && kind !== 'capsule') {
    return instancedShape(kind, geometry, scale);
  }
  const abs = new Vector3(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z));
  const box = geometry.box.isEmpty() ? new Box3(new Vector3(-0.5, -0.5, -0.5), new Vector3(0.5, 0.5, 0.5)) : geometry.box;
  const half = box.getSize(new Vector3()).multiplyScalar(0.5).multiply(abs);
  half.set(Math.max(half.x, MIN_HALF), Math.max(half.y, MIN_HALF), Math.max(half.z, MIN_HALF));
  const center = box.getCenter(new Vector3()).multiply(scale);

  switch (kind) {
    case 'sphere':
      return { kind: 'sphere', radius: Math.max(half.x, half.y, half.z), center };
    case 'capsule': {
      const radius = Math.max(half.x, half.z);
      return { kind: 'capsule', radius, halfHeight: Math.max(half.y - radius, 0.001), center };
    }
    case 'convex':
    case 'mesh': {
      if (geometry.positions.length < 12) break; // Too few points: fall back to a box.
      const points = scaledPoints(geometry.positions, scale);
      if (kind === 'convex') return { kind: 'convex', points, center: new Vector3() };
      return { kind: 'mesh', vertices: points, indices: new Uint32Array(geometry.indices), center: new Vector3() };
    }
    default:
      break;
  }
  return { kind: 'box', halfExtents: half, center };
}

function scaledPoints(positions: readonly number[], scale: Vector3): Float32Array {
  const points = new Float32Array(positions.length);
  for (let i = 0; i < points.length; i += 3) {
    points[i] = positions[i]! * scale.x;
    points[i + 1] = positions[i + 1]! * scale.y;
    points[i + 2] = positions[i + 2]! * scale.z;
  }
  return points;
}

/** Vertices of `geometry` through `matrix`, appended to `out` at `offset`. */
function transformInto(geometry: BufferGeometry, matrix: Matrix4, out: Float32Array, offset: number): void {
  const pos = geometry.getAttribute('position');
  const e = matrix.elements;
  for (let i = 0, o = offset; i < pos.count; i++, o += 3) {
    const x = pos.getX(i);
    const y = pos.getY(i);
    const z = pos.getZ(i);
    out[o] = e[0]! * x + e[4]! * y + e[8]! * z + e[12]!;
    out[o + 1] = e[1]! * x + e[5]! * y + e[9]! * z + e[13]!;
    out[o + 2] = e[2]! * x + e[6]! * y + e[10]! * z + e[14]!;
  }
}

/** An oriented box: `box` (in its own space) through `matrix`. */
function boxPart(box: Box3, matrix: Matrix4): PartSpec {
  const position = new Vector3();
  const rotation = new Quaternion();
  const size = new Vector3();
  matrix.decompose(position, rotation, size);
  const half = box.getSize(new Vector3()).multiplyScalar(0.5);
  half.set(
    Math.max(half.x * Math.abs(size.x), MIN_HALF),
    Math.max(half.y * Math.abs(size.y), MIN_HALF),
    Math.max(half.z * Math.abs(size.z), MIN_HALF),
  );
  return { kind: 'box', halfExtents: half, center: box.getCenter(new Vector3()).applyMatrix4(matrix), rotation };
}

function instancedShape(kind: ShapeKind, geometry: LocalGeometry, scale: Vector3): ShapeSpec {
  const scaling = new Matrix4().makeScale(scale.x, scale.y, scale.z);
  const placed = (matrix: Matrix4): Matrix4 => scaling.clone().multiply(matrix);

  if (kind === 'mesh') {
    // One trimesh: the plain meshes and every copy.
    let vertexCount = geometry.positions.length / 3;
    let indexCount = geometry.indices.length;
    for (const { geometry: g, matrices } of geometry.instanced) {
      const count = g.getAttribute('position').count;
      vertexCount += count * matrices.length;
      indexCount += (g.getIndex()?.count ?? count) * matrices.length;
    }
    if (vertexCount >= 4) {
      const vertices = new Float32Array(vertexCount * 3);
      const indices = new Uint32Array(indexCount);
      vertices.set(scaledPoints(geometry.positions, scale));
      indices.set(geometry.indices);
      let vertexAt = geometry.positions.length / 3;
      let indexAt = geometry.indices.length;
      for (const { geometry: g, matrices } of geometry.instanced) {
        const count = g.getAttribute('position').count;
        const index = g.getIndex();
        for (const matrix of matrices) {
          transformInto(g, placed(matrix), vertices, vertexAt * 3);
          if (index) for (let i = 0; i < index.count; i++) indices[indexAt++] = vertexAt + index.getX(i);
          else for (let i = 0; i < count; i++) indices[indexAt++] = vertexAt + i;
          vertexAt += count;
        }
      }
      return { kind: 'mesh', vertices, indices, center: new Vector3() };
    }
  }

  const parts: PartSpec[] = [];
  const none = new Quaternion();
  if (kind === 'convex' && geometry.positions.length >= 12) {
    parts.push({ kind: 'convex', points: scaledPoints(geometry.positions, scale), center: new Vector3(), rotation: none });
  } else if (!geometry.plainBox.isEmpty()) {
    parts.push(boxPart(geometry.plainBox, scaling));
  }
  for (const { geometry: g, box, matrices } of geometry.instanced) {
    const count = g.getAttribute('position').count;
    for (const matrix of matrices) {
      if (kind === 'convex' && count >= 4) {
        const points = new Float32Array(count * 3);
        transformInto(g, placed(matrix), points, 0);
        parts.push({ kind: 'convex', points, center: new Vector3(), rotation: none });
      } else if (!box.isEmpty()) {
        parts.push(boxPart(box, placed(matrix)));
      }
    }
  }
  if (parts.length === 0) {
    const half = new Vector3(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z)).multiplyScalar(0.5);
    return { kind: 'box', halfExtents: half, center: new Vector3() };
  }
  return { kind: 'compound', parts, center: new Vector3() };
}
