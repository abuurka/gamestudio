// Vector and rotation arguments: every API that takes a position accepts an array or an object.

import { Euler, Quaternion, Vector3 } from 'three';

/** A point or direction: `[x, y, z]`, `{ x, y, z }` or a Three.js `Vector3`. */
export type Vec3Input = readonly [number, number, number] | { readonly x: number; readonly y: number; readonly z: number };

/**
 * A rotation: a quaternion `[x, y, z, w]`, `{ x, y, z, w }` or `Quaternion`, or Euler angles in
 * radians `[x, y, z]` (XYZ order) or a Three.js `Euler`.
 */
export type RotationInput =
  | readonly [number, number, number]
  | readonly [number, number, number, number]
  | { readonly x: number; readonly y: number; readonly z: number; readonly w: number }
  | Euler
  | Quaternion;

/** Reads a `Vec3Input` into `target`. */
export function toVector3(value: Vec3Input, target = new Vector3()): Vector3 {
  if (isArray(value)) return target.set(value[0], value[1], value[2]);
  return target.set(value.x, value.y, value.z);
}

/**
 * Reads a `RotationInput` into `target` (quaternions are normalized). Objects are told apart by
 * shape (a `w` means a quaternion), so values from another copy of three work too.
 */
export function toQuaternion(value: RotationInput, target = new Quaternion()): Quaternion {
  if (isArray(value)) {
    if (value.length === 4) return target.set(value[0], value[1], value[2], value[3]!).normalize();
    return target.setFromEuler(new Euler(value[0], value[1], value[2]));
  }
  const v = value as { x: number; y: number; z: number; w?: number; order?: string };
  if (typeof v.w === 'number') return target.set(v.x, v.y, v.z, v.w).normalize();
  return target.setFromEuler(new Euler(v.x, v.y, v.z, (v.order ?? 'XYZ') as Euler['order']));
}

/** True for `[x, y, z]` and `{ x, y, z }` (an entity, which has `object3d`, is not a point). */
export function isVec3Input(value: unknown): value is Vec3Input {
  if (Array.isArray(value)) return value.length >= 3 && value.slice(0, 3).every((n) => typeof n === 'number');
  if (!value || typeof value !== 'object' || 'object3d' in value) return false;
  const v = value as Record<string, unknown>;
  return typeof v.x === 'number' && typeof v.y === 'number' && typeof v.z === 'number';
}

function isArray(value: unknown): value is readonly number[] {
  return Array.isArray(value);
}
