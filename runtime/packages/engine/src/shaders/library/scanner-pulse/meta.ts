// ScannerPulse: a ring sweeps out from an origin and leaves a grid and contour read-out behind it.
// DOM-free and tiny: the fields, the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  origin: field.vec3([0, 0, 0], {
    description: 'World point the ring starts from. From code: ctx.shaders.set("GS_Ground", { origin: [x, y, z], startAt: ctx.time })',
  }),
  startAt: field.float(0, { unit: 'time', description: 'Game time of the (first) pulse. Set it to ctx.time to trigger one now' }),
  interval: field.float(4, { min: 0, unit: 'time', description: 'Seconds between repeating pulses; 0: one pulse per startAt' }),
  reach: field.float(30, { min: 0.1, unit: 'distance', description: 'How far the ring travels (metres)' }),
  sweep: field.float(1.5, { min: 0.05, unit: 'time', description: 'Seconds the ring takes to reach its full distance' }),
  color: field.color([0.1, 0.87, 1, 1], { description: 'Colour of the ring and the read-out' }),
  strength: field.float(1, { min: 0, description: 'Brightness of the pulse (above 1 it blooms; 0 turns it off)' }),
  gridSize: field.float(1, { min: 0.01, unit: 'distance', description: 'Spacing of the floor grid and contour lines (metres)' }),
};

export const ScannerPulse = defineShader('ScannerPulse', {
  description:
    'A scan ring sweeps out from an origin across everything drawn with this material, leaving a floor grid, ' +
    'contour lines and lit silhouettes that fade behind it. Repeats every interval, or set startAt (and origin) ' +
    'from code. Keeps the material. A few line tests per pixel, only while a pulse runs.',
  category: 'Magic & Sci-fi',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', values: { reach: 6, interval: 0 }, t: 0.45 },
  load: () => import('./impl'),
});
