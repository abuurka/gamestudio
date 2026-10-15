import { defineComponent, field } from '@gamestudio/engine/schema';

export const TrackChunk = defineComponent('TrackChunk', {
  description: 'The root of a track chunk prefab, at its start. TrackSystem lays chunks end to end toward Blender +Y.',
  category: 'Runner',
  fields: {
    length: field.float(30, { min: 1, unit: 'distance', description: 'Metres from this root to where the next chunk starts (Blender +Y)' }),
    weight: field.float(1, { min: 0, description: 'How often it is picked, relative to the other chunks (0: never)' }),
    minDistance: field.float(0, { min: 0, unit: 'distance', description: 'Picked only from this many metres into the run (difficulty)' }),
  },
});
