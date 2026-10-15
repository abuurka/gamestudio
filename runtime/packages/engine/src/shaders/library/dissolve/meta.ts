// Dissolve: the material burns away along a glowing, noisy edge (progress 0–1, or timed from startAt).
// DOM-free and tiny: the fields, the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  progress: field.float(0, {
    min: 0,
    max: 1,
    description: 'How much is gone: 0 whole, 1 gone. From code: ctx.shaders.set(enemy, { progress: t }) each frame',
  }),
  startAt: field.float(-1, {
    unit: 'time',
    description: 'Game time a timed dissolve starts (-1: off). From code: ctx.shaders.set(enemy, { startAt: ctx.time }), nothing per frame',
  }),
  duration: field.float(1.5, { min: 0.05, unit: 'time', description: 'Seconds the timed dissolve takes' }),
  reverse: field.bool(false, { description: 'The timed dissolve runs backwards: the object appears (a spawn-in)' }),
  direction: field.vec3([0, -1, 0], {
    description: "Way the cut travels, in the object's own axes: (0, -1, 0) head to feet; (0, 0, 0) holes everywhere at once",
  }),
  noise: field.float(0.35, { min: 0, max: 1, description: 'How ragged the travelling edge is: 0 a clean cut, 1 only holes' }),
  noiseScale: field.float(4, { min: 0.1, description: 'Size of the holes: about this many blobs across the object' }),
  edgeColor: field.color([1, 0.32, 0.05, 1], { description: 'Colour of the glowing edge' }),
  edgeWidth: field.float(0.05, { min: 0.005, max: 0.5, description: 'Width of the glowing edge, as a share of the whole dissolve' }),
  glow: field.float(3, { min: 0, description: 'Brightness of the edge (above 1 it blooms)' }),
};

export const Dissolve = defineShader('Dissolve', {
  description:
    'Burns the material away along a glowing, noisy edge that sweeps across the object, with a scorched band ' +
    'ahead of it; the shadow dissolves too. Keeps the material and works on characters. Drive progress, or set ' +
    'startAt to a game time. Two noise lookups per pixel while dissolving, none otherwise.',
  category: 'Magic & Sci-fi',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', values: { progress: 0.45 }, t: 1 },
  load: () => import('./impl'),
});
