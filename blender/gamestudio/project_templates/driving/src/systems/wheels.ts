import { defineSystem, type Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Car } from '../components/car';
import { Wheel } from '../components/wheel';
import { carState } from '../lib/car-state';

const STEER_ANGLE = 0.45; // radians at full lock
const PUFF_EVERY = 0.08; // seconds between puffs from one wheel
const DUSTY_SPEED = 6; // m/s off the road that kicks up dust
const SLIDE_SPEED = 4.5; // m/s sideways on the road that smokes the tyres (a drift)

const lastPuff = new Map<Entity, number>(); // ctx.time of each wheel's last puff
const contact = new Vector3();

/** Spins wheels with their car's speed and turns the front ones; the rear ones kick up dust off the road and smoke in drifts. */
export default defineSystem({
  name: 'WheelSystem',
  query: [Wheel],

  onAdd(_ctx, wheel) {
    wheel.object3d.rotation.order = 'YXZ'; // steer about the car's up axis, then spin about the axle
  },
  onRemove(_ctx, wheel) {
    lastPuff.delete(wheel);
  },

  update(ctx, wheels) {
    for (const wheel of wheels) {
      const car = ctx.world.get(wheel.parentId);
      if (!car?.has(Car)) continue;
      const state = carState(car);
      const { radius, steers } = wheel.get(Wheel);
      wheel.object3d.rotation.x += (state.speed / radius) * ctx.dt;
      if (steers) {
        wheel.object3d.rotation.y = state.steer * STEER_ANGLE;
        continue;
      }
      const slide = Math.sqrt(Math.max(0, state.velocity.lengthSq() - state.speed * state.speed));
      const dusty = state.offRoad && Math.abs(state.speed) > DUSTY_SPEED;
      const smoky = !state.offRoad && slide > SLIDE_SPEED;
      if (!(dusty || smoky) || ctx.time - (lastPuff.get(wheel) ?? -1) < PUFF_EVERY) continue;
      lastPuff.set(wheel, ctx.time);
      wheel.object3d.getWorldPosition(contact).y -= radius * 0.8;
      if (dusty) ctx.vfx.burst(contact, 'dust', { amount: 0.4 });
      else ctx.vfx.burst(contact, 'smoke', { scale: 0.45, amount: 0.25, color: '#d9d9d9' });
    }
  },
});
