// Water: a lake, sea or pond surface. The fields and the gallery preview; the GLSL is in impl.ts.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  waveLength: field.float(4, {
    min: 0.1,
    unit: 'distance',
    description: 'Length of the longest waves: about 1 m for a pond, 4 m for a lake, 15 m or more for the open sea',
  }),
  choppiness: field.float(0.6, { min: 0, max: 2, description: 'How rough the surface is: 0 is a still mirror, 1 a windy sea' }),
  speed: field.float(1, { min: 0, description: 'Wave speed (1 = the natural speed of waves that long)' }),
  direction: field.float(0, { unit: 'angle', description: "Direction the waves travel, in radians (Blender shows degrees), from Blender's +X axis toward +Y" }),
  foam: field.float(0.2, { min: 0, max: 1, description: 'Whitecaps on the wave crests (0 = none)' }),
  reflection: field.float(1, { min: 0, max: 2, description: 'Strength of the sky reflected in the water' }),
};

export const Water = defineShader('Water', {
  description:
    'Animated water for lakes, seas and ponds of any size: rolling waves, sky reflection with sun glints, whitecaps. ' +
    "The material's colour tints the water and its alpha is how see-through it is from above. Waves bend the light " +
    'only (the surface stays flat), so a single quad works. Cheap: no textures, about ten waves per pixel.',
  category: 'Water',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', t: 2, values: { waveLength: 2.5, foam: 0.3 } },
  load: () => import('./impl'),
});
