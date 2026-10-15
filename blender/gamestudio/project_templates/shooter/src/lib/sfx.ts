// Sound effects. Importing with ?url lets Vite serve them in development and copy them into builds.
import enemyDown from '../../assets/sfx/enemy-down.wav?url';
import hit from '../../assets/sfx/hit.wav?url';
import hurt from '../../assets/sfx/hurt.wav?url';
import shoot from '../../assets/sfx/shoot.wav?url';
import wave from '../../assets/sfx/wave.wav?url';

export const sfx = { enemyDown, hit, hurt, shoot, wave };
