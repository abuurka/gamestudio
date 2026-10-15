// Lava: molten rock with drifting crust plates, for lava lakes, ground and channels. The fields and
// the gallery preview; the GLSL is in impl.ts.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([1, 0.3, 0.05, 1], { description: 'Glow colour of the molten lava (linear RGBA); the hottest parts burn whiter' }),
  heat: field.float(0.4, { min: 0, max: 1, description: 'How molten it is: 0 a dark crust with glowing cracks, 1 nearly all open lava' }),
  scale: field.float(1.2, { min: 0.01, description: 'Size of the crust plates: metres (world mapping) or UV lengths (uv mapping)' }),
  flow: field.vec2([0.25, 0], {
    description: "Flow velocity along Blender's X and Y in m/s (world mapping), or along U and V in UV lengths per second (uv mapping)",
  }),
  mapping: field.enum(['world', 'uv'], 'world', {
    description: 'world: projected from above, for lakes and ground; uv: along the UVs, for channels (U across from bank to bank, V along)',
  }),
  brightness: field.float(1, { min: 0, description: 'Glow multiplier; above 1 blooms more' }),
};

export const Lava = defineShader('Lava', {
  description:
    'Flowing lava: dark crust plates that drift, crack and re-form over glowing molten rock, hottest where the crust ' +
    'opens. Lights itself (glow), so it shines in the dark; the crust is lit and shadowed like rock. Replaces the ' +
    "material's look. Moderate: two cell-noise lookups (9 cells each) per pixel.",
  category: 'Water',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', t: 2, values: { scale: 0.9 } },
  load: () => import('./impl'),
});
