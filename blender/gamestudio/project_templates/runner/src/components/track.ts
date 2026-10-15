import { defineComponent, field } from '@gamestudio/engine/schema';

export const Track = defineComponent('Track', {
  description: 'Where the endless track starts (this empty) and how it streams: chunks appear ahead of the runner and go behind it.',
  category: 'Runner',
  fields: {
    ahead: field.float(150, { min: 30, unit: 'distance', description: 'Metres of track kept built ahead of the runner' }),
    behind: field.float(20, { min: 5, unit: 'distance', description: 'Chunks that end this many metres behind the runner are destroyed' }),
    warmup: field.float(60, { min: 0, unit: 'distance', description: 'No obstacles in the first metres of the run' }),
    obstacles: field.string('Hurdle:3, Crates:2, Boulder:2', { description: 'Prefabs for obstacle sockets, "Name:weight, ..."' }),
    coins: field.string('CoinLine:3, CoinArc:1', { description: 'Prefabs for coin sockets' }),
    pickups: field.string('Magnet, Shield', { description: 'Prefabs for pickup sockets' }),
    chunks: field.int(0, { min: 0, description: 'Chunks spawned so far (runtime)' }),
  },
});
