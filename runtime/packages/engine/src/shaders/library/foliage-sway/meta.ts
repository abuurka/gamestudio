// FoliageSway: crowns, bushes and leaf cards swaying in gusty wind. DOM-free and tiny: the fields, the
// gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  wind: field.float(0.5, { min: 0, max: 1.5, description: 'Wind strength: 0 calm, 1 a gale' }),
  windAngle: field.float(0.35, {
    unit: 'angle',
    description: 'Direction the wind blows towards, in radians (Blender shows degrees), seen from above: 0 = +X, counter-clockwise. Give grass and flags the same to gust together',
  }),
  speed: field.float(1, { min: 0, description: 'Speed of the sway, the gusts and the rustle' }),
  sway: field.float(1, { min: 0, max: 4, description: 'How far the crown bends' }),
  rustle: field.float(1, { min: 0, max: 4, description: 'Quick leaf flutter over the surface. 0 for trunks and stiff plants' }),
  height: field.float(4, {
    min: 0.1,
    unit: 'distance',
    description: "Height above the object's origin where the bend reaches its full amount; lower parts bend less",
  }),
  translucency: field.float(0.5, { min: 0, max: 2, description: 'Sun glowing through the leaves and their thin edges. 0 for bark' }),
  leaves: field.enum(['solid', 'cards'], 'solid', {
    description: "cards: alpha-cut leaf cards (the texture's see-through parts are cut out, both sides drawn)",
  }),
};

export const FoliageSway = defineShader('FoliageSway', {
  description:
    'Leaves, crowns and bushes sway in gusty wind with a quick leafy rustle, bending more the higher they are ' +
    "above the object's origin (each GPU instance on its own, in its own phase); shadows move too. Put it on the " +
    'leaves (GS_Leaves); give the bark the same values with rustle and translucency 0 to bend the trunk along.',
  category: 'Nature',
  builtin: true,
  fields,
  preview: { mesh: 'tree', t: 2, values: { wind: 0.8 } },
  load: () => import('./impl'),
});
