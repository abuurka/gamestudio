import { defineComponent, field } from '@gamestudio/engine/schema';

export const WaveSpawner = defineComponent('WaveSpawner', {
  description: 'Sends waves of enemy prefabs from the SpawnPoints; the next wave comes once a wave is cleared.',
  category: 'Shooter',
  fields: {
    enemies: field.string('Grunt, Runner@3', { description: 'Enemy prefabs, comma-separated; Name@N joins from wave N' }),
    firstWave: field.int(4, { min: 1, description: 'Enemies in wave 1' }),
    perWave: field.int(2, { min: 0, description: 'Extra enemies in each later wave' }),
    spawnInterval: field.float(0.6, { min: 0.05, unit: 'time', description: 'Seconds between spawns' }),
    breather: field.float(3, { min: 0, unit: 'time', description: 'Seconds between waves (and before the first)' }),
  },
});
