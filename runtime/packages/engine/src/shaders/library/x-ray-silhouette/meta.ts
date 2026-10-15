// X-Ray Silhouette: the object shows through walls as a coloured fresnel silhouette.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([0.078, 0.644, 1, 1], { description: 'Colour of the silhouette (allies blue, enemies red, …)' }),
  strength: field.float(1, { min: 0, max: 3, description: 'Brightness of the silhouette; 0 switches it off (set it per entity from code)' }),
  bands: field.float(1, { min: 0, max: 1, description: 'Scan bands crawling up the silhouette (0: none)' }),
  pingAt: field.float(-1000, {
    unit: 'time',
    description: 'Game time of a ping: a bright band sweeps up the silhouette once. ctx.shaders.set(entity, { pingAt: ctx.time })',
  }),
  minDepth: field.float(0.3, {
    min: 0,
    max: 5,
    unit: 'distance',
    description: 'How far in front something must be to count as a wall, so the object’s own limbs do not show through it',
  }),
};

export const XRaySilhouette = defineShader('XRaySilhouette', {
  description:
    'Shows the object through walls as a glowing fresnel silhouette with scan bands, so players see allies, enemies or ' +
    'loot behind cover. The object itself keeps its look. One extra draw per mesh (with its skeleton or instances).',
  category: 'Gameplay',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', occluder: true },
  load: () => import('./impl'),
});
