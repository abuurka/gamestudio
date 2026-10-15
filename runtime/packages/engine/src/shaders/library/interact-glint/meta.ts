// Interact Glint: a periodic specular sweep, sparkles and a rim on things the player can pick up or use.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([0.212, 0.871, 1, 1], { description: 'Colour of the glint and the rim' }),
  strength: field.float(1, {
    min: 0,
    max: 2,
    description: '0: off; a little: a faint idle glint; 1: the player is near (set it from code by distance)',
  }),
  period: field.float(2.4, { min: 0.6, max: 20, unit: 'time', description: 'Seconds between two sweeps of light' }),
  sparkle: field.float(1, { min: 0, max: 3, description: 'Brightness of the small twinkling stars (0: none)' }),
};

export const InteractGlint = defineShader('InteractGlint', {
  description:
    'Marks things the player can pick up or use: a band of light sweeps across the object every few seconds, small ' +
    'stars twinkle on it and its edges glow. Raise Strength as the player comes near (0 switches it off). Keeps the ' +
    'material’s look; cheap (no texture reads).',
  category: 'Gameplay',
  builtin: true,
  fields,
  preview: { mesh: 'box', camera: 'near', color: [0.3, 0.12, 0.04, 1], t: 1.8 },
  load: () => import('./impl'),
});
