import { createGame } from '@gamestudio/engine';

// Register every component, system and game shader (each module calls defineComponent/defineSystem/defineShader).
import.meta.glob('./components/*.ts', { eager: true });
import.meta.glob('./systems/*.ts', { eager: true });
import.meta.glob('./shaders/*.ts', { eager: true });

createGame({ canvas: '#game', startScene: 'main' });
