// Selection Outline: a constant-width outline around the object, dashed where walls hide it.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([1, 0.54, 0.042, 1], { description: 'Colour of the outline' }),
  width: field.float(3, { min: 0, max: 20, description: 'Line width in screen pixels: the same at any distance and size' }),
  intensity: field.float(1.5, { min: 0, max: 10, description: 'Brightness of the line (above 1 it glows with bloom)' }),
  pulse: field.float(0.3, { min: 0, max: 1, description: 'How much the line breathes (0: steady)' }),
  strength: field.float(1, {
    min: 0,
    max: 1,
    description: '0 switches the outline off: set it per entity from code on hover or selection',
  }),
  hidden: field.bool(true, { description: 'Where a wall hides the object, show a dashed edge and a hatched fill through it' }),
};

export const SelectionOutline = defineShader('SelectionOutline', {
  description:
    'An outline of constant screen width around the object (selection, hover, targets) that breathes softly, with a ' +
    'dashed, hatched silhouette where walls hide it. Switch it per entity with ctx.shaders.set(entity, { strength: 1 }). ' +
    'The object keeps its look. For closed meshes (a flat card shows its back face). Two extra draws per mesh.',
  category: 'Gameplay',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', occluder: true },
  load: () => import('./impl'),
});
