// The geometry of an inverted-hull outline: a mesh's own buffers plus normals smoothed over shared
// positions (`outlineNormal`), so pushing every vertex out along them keeps the hull closed at hard
// edges (a crate's corners). Used by the Selection Outline library shader and the editor's
// selection outline in the Scene view (game/selection.ts). DOM-free.

import { BufferAttribute, BufferGeometry, Vector3 } from 'three';

/**
 * Draws like its source (it shares the source's index, positions, skin weights and morphs) and adds
 * `outlineNormal`. Disposing it frees only its own normals: the shared buffers belong to the source
 * (which may live on, in the parse cache).
 */
export class HullGeometry extends BufferGeometry {
  constructor(readonly source: BufferGeometry) {
    super();
    this.setIndex(source.getIndex());
    for (const [name, attribute] of Object.entries(source.attributes)) {
      if (name === 'position' || name === 'skinIndex' || name === 'skinWeight') this.setAttribute(name, attribute);
    }
    if (source.morphAttributes.position) this.morphAttributes.position = source.morphAttributes.position;
    this.morphTargetsRelative = source.morphTargetsRelative;
    for (const group of source.groups) this.addGroup(group.start, group.count, group.materialIndex);
    this.setDrawRange(source.drawRange.start, source.drawRange.count);
    if (!source.boundingBox) source.computeBoundingBox();
    if (!source.boundingSphere) source.computeBoundingSphere();
    this.boundingBox = source.boundingBox!.clone();
    this.boundingSphere = source.boundingSphere!.clone();
    this.setAttribute('outlineNormal', new BufferAttribute(smoothNormals(source), 3));
  }

  override dispose(): void {
    // The renderer frees the buffers of the attributes a disposed geometry still has: keep only ours.
    for (const name of Object.keys(this.attributes)) if (name !== 'outlineNormal') this.deleteAttribute(name);
    this.setIndex(null);
    this.morphAttributes = {};
    super.dispose();
  }
}

/**
 * Face normals summed over the vertices that share a position, each weighted by the triangle's angle
 * there (so a cube's corner gets the exact diagonal however its faces are split into triangles).
 */
export function smoothNormals(geometry: BufferGeometry): Float32Array {
  const position = geometry.getAttribute('position');
  const index = geometry.getIndex();
  const count = position.count;
  const slotOf = new Uint32Array(count);
  const slots = new Map<string, number>();
  for (let i = 0; i < count; i++) {
    const key = `${Math.round(position.getX(i) * 1e4)},${Math.round(position.getY(i) * 1e4)},${Math.round(position.getZ(i) * 1e4)}`;
    let slot = slots.get(key);
    if (slot === undefined) slots.set(key, (slot = slots.size));
    slotOf[i] = slot;
  }
  const sums = new Float64Array(slots.size * 3);
  const corners = [new Vector3(), new Vector3(), new Vector3()] as const;
  const face = new Vector3();
  const u = new Vector3();
  const v = new Vector3();
  const ids = [0, 0, 0];
  const triangles = Math.floor((index ? index.count : count) / 3);
  for (let t = 0; t < triangles; t++) {
    for (let k = 0; k < 3; k++) {
      ids[k] = index ? index.getX(t * 3 + k) : t * 3 + k;
      corners[k].fromBufferAttribute(position, ids[k]!);
    }
    face.subVectors(corners[1], corners[0]).cross(v.subVectors(corners[2], corners[0]));
    if (face.lengthSq() < 1e-30) continue; // degenerate
    face.normalize();
    for (let k = 0; k < 3; k++) {
      const angle = u.subVectors(corners[(k + 1) % 3]!, corners[k]).angleTo(v.subVectors(corners[(k + 2) % 3]!, corners[k]));
      const s = slotOf[ids[k]!]! * 3;
      sums[s] = sums[s]! + face.x * angle;
      sums[s + 1] = sums[s + 1]! + face.y * angle;
      sums[s + 2] = sums[s + 2]! + face.z * angle;
    }
  }
  const normal = geometry.getAttribute('normal');
  const out = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const s = slotOf[i]! * 3;
    u.set(sums[s]!, sums[s + 1]!, sums[s + 2]!);
    if (u.lengthSq() > 1e-20) u.normalize();
    else if (normal) u.fromBufferAttribute(normal, i);
    else u.set(0, 1, 0);
    out[i * 3] = u.x;
    out[i * 3 + 1] = u.y;
    out[i * 3 + 2] = u.z;
  }
  return out;
}

