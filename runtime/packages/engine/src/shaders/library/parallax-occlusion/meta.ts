// ParallaxOcclusion: carved relief from a height map (after FX Lab "parallax-ruins").

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

const IMAGES = ['.webp', '.png', '.jpg', '.jpeg'];

export const fields = {
  height: field.asset(IMAGES, {
    description: "Height map in the material's UV, white = high (red channel). Empty: procedural flagstones",
  }),
  size: field.float(3, { min: 0.01, unit: 'distance', description: 'UV units per repeat of the height map (metres on GameStudio kits)' }),
  depth: field.float(0.05, { min: 0, max: 1, unit: 'distance', description: 'Depth of the relief, metres' }),
  steps: field.int(24, {
    min: 4,
    max: 64,
    description: 'Height samples per pixel at grazing angles (fewer head-on): more is cleaner and slower',
  }),
  shadow: field.float(1, { min: 0, max: 1, description: 'Self-shadowing from the sun inside the relief' }),
  gaps: field.float(0.5, { min: 0, max: 1, description: 'How much darker the low parts (joints, cracks) get' }),
};

export const ParallaxOcclusion = defineShader('ParallaxOcclusion', {
  description:
    'Deep carved relief on a flat surface (paving, brick, carved stone) by parallax occlusion mapping, with ' +
    'self-shadowing: the material’s own textures follow the relief. Height from an image, or procedural flagstones. ' +
    'Silhouettes stay flat. Up to `steps` + 21 texture reads per pixel: use on floors and walls, not on everything.',
  category: 'Surfaces',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', values: { size: 0.375 } }, // the plane's UV spans 8 m: 3 m stones' repeat
  load: () => import('./impl'),
});
