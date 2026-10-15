// GrassWind: blades bending from the base under travelling gusts. DOM-free and tiny: the fields, the
// gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  wind: field.float(0.5, { min: 0, max: 1.5, description: 'Wind strength: 0 calm, 1 a gale' }),
  windAngle: field.float(0.35, {
    unit: 'angle',
    description: 'Direction the wind blows towards, in radians (Blender shows degrees), seen from above: 0 = +X, counter-clockwise. Give trees and flags the same to gust together',
  }),
  speed: field.float(1, { min: 0, description: 'Speed of the sway, the gusts and the flutter' }),
  bend: field.float(1, { min: 0, max: 3, description: 'How far blades bend over' }),
  flutter: field.float(1, { min: 0, max: 4, description: 'Quick sideways flutter of the tips' }),
  sheen: field.float(0.6, { min: 0, max: 1, description: 'Gust fronts turn the bent blades pale, so waves roll visibly over the field' }),
  translucency: field.float(0.6, { min: 0, max: 2, description: 'Sun glowing through the blades' }),
  rootShade: field.float(0.5, { min: 0, max: 1, description: 'Blades darken towards their base, where they shade each other' }),
};

export const GrassWind = defineShader('GrassWind', {
  description:
    'Grass blades and clumps bend from their base under gust fronts rolling across the field, with flutter, ' +
    'a pale sheen where gusts pass and sun through the blades. Each mesh or GPU instance (geometry-nodes ' +
    'scatter) bends from its lowest point to its top; shadows move too.',
  category: 'Nature',
  builtin: true,
  fields,
  preview: { mesh: 'grass', t: 3, values: { wind: 0.6 } },
  load: () => import('./impl'),
});
