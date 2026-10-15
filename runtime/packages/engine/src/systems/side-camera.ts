// SideCamera: the camera of a side-scroller (or a top-down game). It slides across the screen plane
// after its target and never turns, so the depth stays put; with a dead zone, look-ahead, smoothing,
// level bounds and, for orthographic pixel art, whole-pixel steps.

import { Box3, Quaternion, Vector3, type Camera, type Object3D, type OrthographicCamera, type PerspectiveCamera } from 'three';
import type { SystemContext } from '../context';
import type { Entity } from '../ecs/entity';
import { defineSystem } from '../ecs/system';
import { findCamera } from '../game/camera';
import { SideCamera } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import { cameraCut } from './camera-cut';

type SideCameraValues = ComponentValues<typeof SideCamera>;

/** How fast the look-ahead swings to the other side (1/s). */
const LOOK_RATE = 2;
/** Target speed along the screen (m/s) from which the look-ahead turns to its direction. */
const LOOK_SPEED = 0.5;

interface ViewState {
  /** Where the camera centre is, along the screen's right and up (m), before pixel snapping. */
  x: number;
  y: number;
  /** Centre of the dead zone; the target pushes it along. */
  fx: number;
  fy: number;
  /** The look-ahead now (m along right) and the direction it heads for (-1, 0, 1). */
  ahead: number;
  dir: number;
  /** The target's screen x on the previous frame. */
  lastTx: number;
  /** The camera's distance along its view axis, kept as it was placed. */
  depth: number;
}

const states = new WeakMap<Entity, ViewState>();
const rightAxis = new Vector3();
const upAxis = new Vector3();
const backAxis = new Vector3();
const quat = new Quaternion();
const camPos = new Vector3();
const targetPos = new Vector3();
const goal = new Vector3();
const box = new Box3();
const emptyAt = new Vector3();
const emptyTurn = new Quaternion();
const emptySize = new Vector3();

/** What a SideCamera shows of the level, in screen-plane metres: the view's half size. */
function halfView(camera: Camera, distance: number): { w: number; h: number } {
  if ((camera as OrthographicCamera).isOrthographicCamera) {
    const c = camera as OrthographicCamera;
    return { w: (c.right - c.left) / (2 * c.zoom), h: (c.top - c.bottom) / (2 * c.zoom) };
  }
  if ((camera as PerspectiveCamera).isPerspectiveCamera) {
    const c = camera as PerspectiveCamera;
    const h = (Math.max(distance, c.near) * Math.tan((c.fov * Math.PI) / 360)) / c.zoom;
    return { w: h * c.aspect, h };
  }
  return { w: 0, h: 0 };
}

/** Projects an axis-aligned box (infinite on open axes) onto `axis`: `[lo, hi]`. */
function span(min: Vector3, max: Vector3, axis: Vector3): [number, number] {
  let lo = 0;
  let hi = 0;
  for (let i = 0; i < 3; i++) {
    const a = axis.getComponent(i);
    if (Math.abs(a) < 1e-6) continue;
    const p = min.getComponent(i) * a;
    const q = max.getComponent(i) * a;
    lo += Math.min(p, q);
    hi += Math.max(p, q);
  }
  return [lo, hi];
}

/** The level box from the bounds object or the boundsMin/boundsMax fields; `false` if there is none. */
function levelBox(ctx: SystemContext, f: SideCameraValues): boolean {
  const bounds = f.bounds ? ctx.world.get(f.bounds) : undefined;
  if (bounds) {
    box.setFromObject(bounds.object3d);
    if (box.isEmpty()) {
      // No mesh: a Cube empty (display size 1) spans its position ± its scale.
      const object = bounds.object3d;
      object.updateWorldMatrix(true, false);
      object.matrixWorld.decompose(emptyAt, emptyTurn, emptySize);
      emptySize.set(Math.abs(emptySize.x), Math.abs(emptySize.y), Math.abs(emptySize.z));
      box.min.subVectors(emptyAt, emptySize);
      box.max.addVectors(emptyAt, emptySize);
    }
    return !box.isEmpty();
  }
  let any = false;
  for (let i = 0; i < 3; i++) {
    const lo = f.boundsMin[i]!;
    const hi = f.boundsMax[i]!;
    const open = !(hi > lo);
    box.min.setComponent(i, open ? -Infinity : lo);
    box.max.setComponent(i, open ? Infinity : hi);
    any ||= !open;
  }
  return any;
}

/** Keeps `centre` so the view (half size `half`) stays inside `[lo, hi]`; centres it if the view is wider. */
function clampView(centre: number, half: number, lo: number, hi: number): number {
  if (hi - lo <= 2 * half) return Number.isFinite(lo + hi) ? (lo + hi) / 2 : centre;
  return Math.min(Math.max(centre, lo + half), hi - half);
}

/** Moves the camera after its target; `snap` (or a cut: a teleport or snapCamera) puts it in place, centred again. */
function place(ctx: SystemContext, e: Entity, alpha: number, snap: boolean): void {
  const f = e.get(SideCamera);
  const target = ctx.world.get(f.target);
  const camera = findCamera(e.object3d);
  if (!target || !camera) return;
  if (cameraCut(e, target, ctx.dt)) snap = true;
  if (snap) alpha = 1;

  camera.updateWorldMatrix(true, false);
  camera.getWorldPosition(camPos);
  camera.getWorldQuaternion(quat);
  rightAxis.set(1, 0, 0).applyQuaternion(quat);
  upAxis.set(0, 1, 0).applyQuaternion(quat);
  backAxis.set(0, 0, 1).applyQuaternion(quat);
  target.object3d.getWorldPosition(targetPos);
  const tx = targetPos.dot(rightAxis) + f.offset[0];
  const ty = targetPos.dot(upAxis) + f.offset[1];
  const cx = camPos.dot(rightAxis);
  const cy = camPos.dot(upAxis);

  let s = states.get(e);
  if (!s || snap) {
    s = { x: tx, y: ty, fx: tx, fy: ty, ahead: 0, dir: 0, lastTx: tx, depth: camPos.dot(backAxis) };
    states.set(e, s);
  } else if (Math.abs(camPos.dot(backAxis) - s.depth) > 1e-4) {
    s.depth = camPos.dot(backAxis); // moved in depth from code (a zoom): keep the new depth
  }
  const followX = f.follow !== 'vertical';
  const followY = f.follow !== 'horizontal';
  if (!followX) s.x = s.fx = cx;
  if (!followY) s.y = s.fy = cy;

  // The dead zone: the target moves freely inside it and pushes it along at its edges.
  const hw = f.deadZone[0] / 2;
  const hh = f.deadZone[1] / 2;
  if (followX) s.fx = Math.min(Math.max(s.fx, tx - hw), tx + hw);
  if (followY) s.fy = Math.min(Math.max(s.fy, ty - hh), ty + hh);

  // Look-ahead: towards where the target heads along the screen, kept while it stands still. At
  // most half of the half width, so a narrow (portrait) view keeps the target well on screen.
  const half = halfView(camera, -goal.subVectors(targetPos, camPos).dot(backAxis));
  const dt = ctx.dt;
  if (dt > 0 && !snap) {
    const speed = (tx - s.lastTx) / dt;
    if (Math.abs(speed) > LOOK_SPEED) s.dir = Math.sign(speed);
    const ahead = s.dir * Math.min(f.lookAhead, half.w > 0 ? half.w / 2 : f.lookAhead);
    s.ahead += (ahead - s.ahead) * (1 - Math.exp(-LOOK_RATE * dt));
  }
  s.lastTx = tx;
  if (!followX) s.ahead = 0;

  s.x += (s.fx + s.ahead - s.x) * alpha;
  s.y += (s.fy - s.y) * alpha;

  // Level bounds: the view (at the target's depth, for a perspective camera) stays inside them.
  if (levelBox(ctx, f)) {
    const [xlo, xhi] = span(box.min, box.max, rightAxis);
    const [ylo, yhi] = span(box.min, box.max, upAxis);
    if (followX) s.x = clampView(s.x, half.w, xlo, xhi);
    if (followY) s.y = clampView(s.y, half.h, ylo, yhi);
  }

  let x = s.x;
  let y = s.y;
  const canvas = ctx.three.renderer?.domElement;
  if (f.pixelSnap && (camera as OrthographicCamera).isOrthographicCamera && canvas && canvas.height > 0) {
    const ortho = camera as OrthographicCamera;
    const metresPerPixel = (ortho.top - ortho.bottom) / ortho.zoom / canvas.height;
    x = Math.round(x / metresPerPixel) * metresPerPixel;
    y = Math.round(y / metresPerPixel) * metresPerPixel;
  }

  // Move the entity (the camera or a rig above it) by what the camera has to move.
  goal.copy(rightAxis).multiplyScalar(x).addScaledVector(upAxis, y).addScaledVector(backAxis, s.depth).sub(camPos);
  if (goal.lengthSq() === 0) return;
  moveWorld(e.object3d, goal);
}

function moveWorld(object: Object3D, delta: Vector3): void {
  const world = object.getWorldPosition(new Vector3()).add(delta);
  if (object.parent) {
    object.parent.updateWorldMatrix(true, false);
    object.position.copy(object.parent.worldToLocal(world));
  } else object.position.copy(world);
  object.updateMatrixWorld(true);
}

/**
 * Moves cameras with `SideCamera` after their target. It snaps into place when it starts, when the target
 * teleports (moves more than 5 m in a frame, faster than 100 m/s) and after `snapCamera()`.
 */
export const SideCameraSystem = defineSystem({
  name: 'SideCameraSystem',
  query: [SideCamera],
  phase: 'late',
  onAdd(ctx, e) {
    place(ctx, e, 1, true);
  },
  update(ctx, entities) {
    for (const e of entities) {
      const smoothing = e.get(SideCamera).smoothing;
      place(ctx, e, smoothing <= 0 ? 1 : 1 - Math.exp(-smoothing * ctx.dt), false);
    }
  },
});
