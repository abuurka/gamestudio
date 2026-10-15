import { defineComponent, field } from '@gamestudio/engine/schema';

export const Shooter = defineComponent('Shooter', {
  description: 'The player: aims at the mouse (or right stick) and fires projectiles while the button is held.',
  category: 'Shooter',
  fields: {
    fireRate: field.float(8, { min: 0.5, max: 30, description: 'Shots per second' }),
    projectile: field.string('Bullet', { description: 'Prefab spawned for each shot' }),
    spread: field.float(0.04, { min: 0, max: 0.5, unit: 'angle', description: 'Random aim error per shot, radians' }),
    muzzleHeight: field.float(1.1, { min: 0, unit: 'distance', description: 'Height of the gun above the feet, metres' }),
  },
});
