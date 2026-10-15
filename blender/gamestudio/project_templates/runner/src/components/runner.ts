import { defineComponent, field } from '@gamestudio/engine/schema';

export const Runner = defineComponent('Runner', {
  description: 'The player: runs forward by itself (Blender +Y) in one of three lanes. Needs a kinematic RigidBody and a capsule Collider.',
  category: 'Runner',
  fields: {
    startSpeed: field.float(10, { min: 1, description: 'Metres per second at the start' }),
    maxSpeed: field.float(20, { min: 1, description: 'Metres per second at the end of the ramp' }),
    rampDistance: field.float(2000, { min: 1, unit: 'distance', description: 'Metres over which speed and difficulty ramp up' }),
    laneSpeed: field.float(14, { min: 1, description: 'Sideways metres per second when switching lanes' }),
    jumpSpeed: field.float(9, { min: 0, description: 'Upward m/s at take-off (1.35 m high with gravity 30)' }),
    gravity: field.float(30, { min: 1, description: 'm/s²' }),
    lane: field.int(1, { min: 0, max: 2, description: 'The lane it is in or heading to: 0 left, 1 middle, 2 right' }),
    distance: field.float(0, { min: 0, description: 'Metres run (runtime)' }),
    speed: field.float(0, { description: 'Metres per second now, negative while a crash knocks it back (runtime)' }),
    magnet: field.float(0, { min: 0, unit: 'time', description: 'Seconds of coin magnet left (runtime)' }),
    shield: field.float(0, { min: 0, unit: 'time', description: 'Seconds of shield left (runtime)' }),
  },
});
