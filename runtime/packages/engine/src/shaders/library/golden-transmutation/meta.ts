// Golden Transmutation: a molten seam climbs the object and leaves polished gold behind (progress 0–1).

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  progress: field.float(1, { min: 0, max: 1, description: '0: untouched; 1: all gold. Animate it from code for the Midas touch' }),
  space: field.enum(['object', 'world'], 'object', {
    description:
      'object: the front climbs each mesh from its lowest to its highest point and moves with it; world: from Bottom to Top (world heights), shared by every mesh',
  }),
  bottom: field.float(0, { unit: 'distance', description: 'World space only: the height where the front starts' }),
  top: field.float(2, { unit: 'distance', description: 'World space only: the height the front reaches at progress 1' }),
  goldColor: field.color([1, 0.54, 0.042, 1], { description: 'Colour of the gold (a metal: this is its reflection colour)' }),
  roughness: field.float(0.2, { min: 0, max: 1, description: 'Roughness of the gold: low is polished' }),
  glitter: field.float(1, { min: 0, max: 3, description: 'Sparkle of tiny facets that catch the sun' }),
  moltenColor: field.color([1, 0.42, 0.08, 1], { description: 'Colour of the molten seam and of the fresh, still hot gold behind it' }),
  heat: field.float(1, { min: 0, max: 5, description: 'Brightness of the molten seam (0: none)' }),
};

export const GoldenTransmutation = defineShader('GoldenTransmutation', {
  description:
    'Turns the material to polished, glittering gold from the bottom up behind a molten seam (progress 0–1), scorching ' +
    'it just ahead of the seam and keeping its look above. Works on characters, props and GPU instances. Two noise ' +
    'lookups per pixel while the effect runs.',
  category: 'Transformations',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', values: { progress: 0.6 } },
  load: () => import('./impl'),
});
