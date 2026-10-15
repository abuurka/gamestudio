// EnergyShield: a hex-lattice force field that ripples where it is hit (hitAt and hitPoint). DOM-free and
// tiny: the fields, the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([0.08, 0.48, 1, 1], { description: "Colour of the field (a saturated Blender base colour tints it)" }),
  intensity: field.float(1, { min: 0, description: 'Brightness (above about 1 the rim blooms)' }),
  lattice: field.enum(['sphere', 'flat'], 'sphere', {
    description: 'sphere: a seamless geodesic lattice for domes and bubbles (origin at the centre); flat: box-projected cells for walls and bridges',
  }),
  cellSize: field.float(0.35, { min: 0.02, unit: 'distance', description: 'Width of a hex cell (metres)' }),
  charge: field.float(1, {
    min: 0,
    max: 1,
    description: 'Shield strength: lower is dimmer, below 0.35 it stutters. From code: ctx.shaders.set(shield, { charge: hp / maxHp })',
  }),
  hitAt: field.float(-10, {
    unit: 'time',
    description: 'Game time of the latest hit: each new value starts a ripple at hitPoint (4 at once). From code: ctx.shaders.set(shield, { hitPoint: p, hitAt: ctx.time })',
  }),
  hitPoint: field.vec3([0, 0, 0], { description: 'World point of the latest hit (set it together with hitAt); the ripple moves with the shield' }),
  rippleSize: field.float(3, { min: 0.1, unit: 'distance', description: 'How far a ripple spreads over the surface (metres)' }),
};

export const EnergyShield = defineShader('EnergyShield', {
  description:
    'A see-through force field: a glowing hex lattice with drifting energy, a bright rim, and ripples, flashes and ' +
    'lit cells where it is hit. Replaces the material (additive light, no shadow). Spheres and domes get a seamless ' +
    'geodesic lattice; walls use lattice "flat". About 20 lattice tests and 4 noise lookups per pixel.',
  category: 'Magic & Sci-fi',
  builtin: true,
  fields,
  preview: { mesh: 'sphere', camera: 'near', values: { cellSize: 0.13, hitAt: 0.7, hitPoint: [0.35, 1.45, 0.8] }, t: 1.1 },
  load: () => import('./impl'),
});
