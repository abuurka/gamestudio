// Petrification: stone creeps up the object from its feet behind a glowing seam (progress 0–1).

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  progress: field.float(1, { min: 0, max: 1, description: '0: untouched; 1: all stone. Animate it from code to petrify' }),
  space: field.enum(['object', 'world'], 'object', {
    description:
      'object: the front climbs each mesh from its lowest to its highest point and moves with it; world: from Bottom to Top (world heights), shared by every mesh',
  }),
  bottom: field.float(0, { unit: 'distance', description: 'World space only: the height where the front starts' }),
  top: field.float(2, { unit: 'distance', description: 'World space only: the height the front reaches at progress 1' }),
  stoneColor: field.color([0.4, 0.39, 0.37, 1], { description: 'Colour of the stone' }),
  glowColor: field.color([1, 0.451, 0.063, 1], { description: 'Colour of the glowing seam at the front (and of glowing cracks)' }),
  glow: field.float(1, { min: 0, max: 10, description: 'Brightness of the seam (0: none)' }),
  crackGlow: field.float(0, { min: 0, max: 1, description: 'How much the cracks in the stone glow (raise it before the statue shatters)' }),
};

export const Petrification = defineShader('Petrification', {
  description:
    'Turns the material to cracked grey stone from the bottom up behind a glowing seam (progress 0–1), keeping its ' +
    'look above the front. Works on characters, props and GPU instances. Seven noise lookups per pixel while stone shows.',
  category: 'Transformations',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', values: { progress: 0.55 } },
  load: () => import('./impl'),
});
