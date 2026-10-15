// HotMetal: blackbody glow by heat 0–1, with forge scale and a crust that skins over as it cools.
// DOM-free and tiny: the fields, the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  heat: field.float(0.7, {
    min: 0,
    max: 1,
    description: 'Temperature: 0 cold (the material as it is), 0.3 dull red, 0.6 orange, 1 white-hot. From code: ctx.shaders.set(blade, { heat })',
  }),
  glow: field.float(1, { min: 0, description: 'Brightness of the glow (it blooms when hot)' }),
  crust: field.float(0.7, { min: 0, max: 1, description: 'How much a dark crust forms as it cools, glowing through its cracks (0: a smooth glow)' }),
  noiseScale: field.float(4, { min: 0.01, description: 'Size of the scale flecks and crust plates: about this many per metre' }),
  coolAt: field.float(-1, {
    unit: 'time',
    description: 'Game time it starts cooling from heat to cold, as when quenched (-1: off). From code: ctx.shaders.set(blade, { coolAt: ctx.time })',
  }),
  coolTime: field.float(4, { min: 0.05, unit: 'time', description: 'Seconds it takes to cool after coolAt' }),
};

export const HotMetal = defineShader('HotMetal', {
  description:
    'Glowing hot metal: blackbody colours from dull red to white by heat, dark forge scale, and a crust that skins ' +
    'over and cracks as it cools (set coolAt to quench it). Keeps the material; at heat 0 it is unchanged. Three ' +
    'noise lookups per pixel while hot, none when cold.',
  category: 'Magic & Sci-fi',
  builtin: true,
  fields,
  preview: { mesh: 'box', camera: 'near', values: { heat: 0.6 } },
  load: () => import('./impl'),
});
