// CarPaint: metallic flake paint under a clear coat (after FX Lab "flake-paint").

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  flip: field.float(0.08, {
    min: -0.5,
    max: 0.5,
    description: 'Colour flip: hue shift towards the flanks, in turns of the colour wheel (0: none)',
  }),
  flakes: field.float(1, { min: 0, max: 4, description: 'Brightness of the metallic flakes' }),
  flakeSize: field.float(0.008, { min: 0.0005, max: 0.1, unit: 'distance', description: 'Size of a flake, metres (object space)' }),
  clearcoat: field.float(1, { min: 0, max: 1, description: 'Strength of the glossy clear coat' }),
  coatRoughness: field.float(0.04, { min: 0, max: 1, description: 'Roughness of the clear coat' }),
};

export const CarPaint = defineShader('CarPaint', {
  description:
    'Car paint over the material’s colour and roughness: the colour flips hue towards the flanks, metallic flakes ' +
    'sparkle in every light (a sheen from afar), under a glossy clear coat. The material becomes a ' +
    'MeshPhysicalMaterial copy of itself (three.js clear coat): a second specular layer per light.',
  category: 'Surfaces',
  builtin: true,
  fields,
  // A deep red: the flip turns the flanks orange.
  preview: { mesh: 'sphere', camera: 'near', color: [0.5, 0.015, 0.015, 1], values: { flip: 0.12 } },
  load: () => import('./impl'),
});
