// Flow: running water along a mesh's UVs (waterfall sheets, rivers, streams). The fields and the
// gallery preview; the GLSL is in impl.ts.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  speed: field.float(0.5, {
    description: "How fast the water runs, in UV lengths per second, toward Blender's V = 0 (down a standing plane); negative reverses",
  }),
  scale: field.float(10, { min: 0.01, description: 'Streaks across one UV length (U); more makes them finer' }),
  stretch: field.float(6, { min: 0.05, description: 'How much longer the streaks are along the flow (V) than across it, in UV lengths' }),
  foam: field.float(0.45, { min: 0, max: 1, description: 'White water: aerated streaks and frothy edges (0 = clear)' }),
  breakup: field.float(0.25, {
    min: 0,
    max: 1,
    description: 'Tears the water into ropes with see-through gaps and frayed edges (needs an Alpha Blend material; opaque ones just darken)',
  }),
};

export const Flow = defineShader('Flow', {
  description:
    'Running water over the material: streaks and white water that travel along the UVs, for waterfall sheets, ' +
    "rivers and streams. Unwrap so U runs across the stream and V along it. The material's colour tints the " +
    'clear water; with Alpha Blend the sheet tears into see-through ropes. Cheap: five noise lookups, no textures.',
  category: 'Water',
  builtin: true,
  fields,
  preview: { mesh: 'cylinder', camera: 'near', t: 1.5, values: { speed: 0.6, foam: 0.6 } },
  load: () => import('./impl'),
});
