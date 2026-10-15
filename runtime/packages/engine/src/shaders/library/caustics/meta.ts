// Caustics: the wavering light under water on floors, walls and anything submerged. The fields and
// the gallery preview; the GLSL is in impl.ts.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([0.75, 0.95, 1, 1], { description: 'Colour of the caustic light (linear RGBA), multiplied by each light’s own colour' }),
  strength: field.float(1, { min: 0, max: 4, description: 'Brightness of the caustic light (0 = none)' }),
  scale: field.float(1, { min: 0.05, unit: 'distance', description: 'Size of the caustic cells in metres' }),
  speed: field.float(1, { min: 0, description: 'How fast the pattern wavers' }),
};

export const Caustics = defineShader('Caustics', {
  description:
    'Wavering caustic light over the material, as on a pool floor or a sunken wreck: bright webs that drift and ' +
    'cross, cast by every lamp and the sun (shadows included), slanted with the light, faintly split into colours. ' +
    'Works on floors, walls and characters; keeps the material as it is. Six reads of a small baked texture per light.',
  category: 'Water',
  builtin: true,
  fields,
  // A sandy pool floor.
  preview: { mesh: 'plane', camera: 'near', t: 1, color: [0.55, 0.45, 0.3, 1], values: { scale: 0.8 } },
  load: () => import('./impl'),
});
