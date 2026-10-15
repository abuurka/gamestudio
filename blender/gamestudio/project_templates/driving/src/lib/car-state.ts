// The live driving state of each car, shared by the systems (like the engine's characterMotion).
import type { Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';

export interface CarState {
  /** Horizontal velocity, m/s (runtime space). */
  velocity: Vector3;
  /** Vertical speed, m/s (+ is up). */
  verticalSpeed: number;
  /** Speed along the car's front, m/s (negative when reversing). */
  speed: number;
  /** Smoothed steering, -1 (full right) to 1 (full left). */
  steer: number;
  /** The surface under the car slows it (grass). */
  offRoad: boolean;
}

const states = new WeakMap<Entity, CarState>();

export function carState(car: Entity): CarState {
  let state = states.get(car);
  if (!state) {
    state = { velocity: new Vector3(), verticalSpeed: 0, speed: 0, steer: 0, offRoad: false };
    states.set(car, state);
  }
  return state;
}

/** 83.42 -> "1:23.42", as the UI shows a time bound with {raceTime:m:ss.00} (hundredths rounded down). */
export function formatTime(seconds: number): string {
  const hundredths = Math.floor(seconds * 100 + 1e-6);
  const minutes = Math.floor(hundredths / 6000);
  const rest = hundredths - minutes * 6000;
  return `${minutes}:${String(Math.floor(rest / 100)).padStart(2, '0')}.${String(rest % 100).padStart(2, '0')}`;
}
