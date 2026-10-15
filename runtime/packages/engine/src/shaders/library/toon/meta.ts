// Toon: cel shading with a rim light and an optional ink outline (after FX Lab "smoke-dome" and the Toon FX pack).

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  bands: field.int(3, { min: 2, max: 8, description: 'Number of flat light tones' }),
  specular: field.float(0.6, { min: 0, max: 4, description: 'Brightness of the hard highlight (its size follows the roughness)' }),
  rim: field.float(0.3, { min: 0, max: 4, description: 'Brightness of the rim light around the silhouette' }),
  rimColor: field.color([1, 1, 1, 1], { description: 'Colour of the rim light' }),
  rimWidth: field.float(0.3, { min: 0, max: 1, description: 'How far the rim reaches in from the silhouette' }),
  outline: field.float(0.02, {
    min: 0,
    max: 1,
    unit: 'distance',
    description: 'Ink outline width, metres (0: none). Drawn as a second, inverted copy of each mesh; not on double-sided materials',
  }),
  outlineColor: field.color([0.03, 0.025, 0.045, 1], { description: 'Colour of the ink outline' }),
};

export const Toon = defineShader('Toon', {
  description:
    'Cel shading over the material’s colour and textures: light in flat bands with hard shadow edges, a hard ' +
    'highlight, a rim light and an ink outline. Works on characters. The outline is an extra draw of each mesh ' +
    '(an inverted hull); set outline to 0 to skip it.',
  category: 'Style',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', color: [0.8, 0.3, 0.08, 1] },
  load: () => import('./impl'),
});
