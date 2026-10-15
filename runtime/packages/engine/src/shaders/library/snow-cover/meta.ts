// SnowCover: snow settling on the upward faces of a Blender material. DOM-free and tiny: the fields,
// the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  amount: field.float(0.7, { min: 0, max: 1, description: '0 bare, 1 deep cover: snow starts on flat tops and spreads down the slopes as it rises' }),
  color: field.color([0.78, 0.82, 0.88, 1], { description: 'Snow colour' }),
  scale: field.float(4, { min: 0.05, unit: 'distance', description: 'Size of the snow patches and the bare gaps between them' }),
  sparkle: field.float(0.5, { min: 0, max: 2, description: 'Ice crystals glinting in the sun' }),
};

export const SnowCover = defineShader('SnowCover', {
  description:
    'Snow settles on everything that faces up (world up, after the normal map), in patches that grow and join ' +
    'as the amount rises, with a damp rim and sparkles in the sun. Keeps the material underneath; animate ' +
    'amount from code for a snowfall that builds up.',
  category: 'Nature',
  builtin: true,
  fields,
  // Dark earth under the snow, so the cover shows.
  preview: { mesh: 'sphere', camera: 'near', color: [0.16, 0.13, 0.1, 1] },
  load: () => import('./impl'),
});
