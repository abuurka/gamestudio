import { defineComponent, field } from '@gamestudio/engine/schema';

export const Car = defineComponent('Car', {
  description: 'An arcade car driven with W/S (or up/down), A/D to steer and Space for the handbrake. Needs a kinematic RigidBody and a box Collider.',
  category: 'Driving',
  fields: {
    maxSpeed: field.float(32, { min: 1, description: 'Top speed on the road, m/s (32 m/s is 115 km/h)' }),
    acceleration: field.float(14, { min: 0, description: 'm/s² with the throttle down' }),
    braking: field.float(30, { min: 0, description: 'm/s² when braking' }),
    reverseSpeed: field.float(8, { min: 0, description: 'Top speed backwards, m/s' }),
    steering: field.float(2.1, { min: 0, unit: 'angle', description: 'Turn rate at full lock, rad/s' }),
    grip: field.float(7, { min: 0, description: 'How fast sideways sliding dies away (higher: more grip)' }),
    handbrakeGrip: field.float(1.2, { min: 0, description: 'Grip with the handbrake on (lower: longer drifts)' }),
  },
});
